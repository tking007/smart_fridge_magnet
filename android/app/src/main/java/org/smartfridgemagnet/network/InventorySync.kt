package org.smartfridgemagnet.network

import android.util.Log
import org.smartfridgemagnet.data.Ingredient
import java.io.IOException

/**
 * 本地库存与后端库存的同步动作集合。
 *
 * 口径是**本地优先、后端 best-effort**：本地那份是用户在 App 里直接看到、也能离线修改的数据，
 * 所以它是权威；后端那份服务于定时任务（餐前渲染菜单要读库存）与网页调试工具。同步失败
 * **不回滚本地写入** —— 否则等于把一个可离线使用的功能绑死在网络上。
 *
 * 按「名称 + 单位」匹配而不是 id：本地 [Ingredient] 没有后端 id，加 id 要连带解决持久化格式与
 * 「离线新增的条目 id 从哪来」两个问题，而这条链路的目的只是让后端库存别一直只增不减。
 * 名称+单位在同一个冰箱里足够唯一（同名同单位的两条本地也会被 addIngredients 合并），代价是
 * 改名等于「删旧增新」—— 这在本地也是同样的语义。
 *
 * 所有方法返回 Result，由调用方决定怎么提示。
 */
object InventorySync {

    private const val TAG = "InventorySync"

    /**
     * 把新增的食材推到后端（批量，后端侧原子）。
     *
     * @return 实际新增条数
     */
    suspend fun pushAdded(
        client: SfmBackendClient,
        ingredients: List<Ingredient>
    ): Result<Int> {
        if (!client.config.configured || ingredients.isEmpty()) return Result.success(0)
        return client.addInventoryItems(ingredients)
    }

    /**
     * 在后端删除对应的库存项。
     *
     * 同名同单位可能对应后端多条（被网页工具或识别自动保存重复录入过），全部删除 ——
     * 只删第一条会让后端留下「看起来已删掉却还在参与菜谱推荐」的残留。
     *
     * @return 实际删除条数
     */
    suspend fun pushDeleted(
        client: SfmBackendClient,
        ingredient: Ingredient
    ): Result<Int> {
        if (!client.config.configured) return Result.success(0)
        return client.fetchInventoryRaw().mapCatching { items ->
            val matched = items.filter { it.matches(ingredient) }
            if (matched.isEmpty()) return@mapCatching 0
            var deleted = 0
            matched.forEach { item ->
                client.deleteInventoryItem(item.id).fold(
                    onSuccess = { deleted++ },
                    onFailure = { e -> Log.w(TAG, "delete #${item.id} failed: ${e.message}") }
                )
            }
            if (deleted == 0) throw IOException("后端删除未成功（共 ${matched.size} 条匹配）")
            deleted
        }
    }

    /**
     * 把编辑后的数量写回后端。
     *
     * 后端找不到对应项时改为新增：这条路径的常见来源是之前某次同步失败，报错让用户重试没有用
     * （本地已经是最新），补一条才是真正对齐。
     */
    suspend fun pushUpdated(
        client: SfmBackendClient,
        ingredient: Ingredient
    ): Result<Unit> {
        if (!client.config.configured) return Result.success(Unit)
        return client.fetchInventoryRaw().mapCatching { items ->
            val matched = items.filter { it.matches(ingredient) }
            if (matched.isEmpty()) {
                client.addInventoryItem(ingredient).getOrThrow()
                return@mapCatching
            }
            // 多条匹配时把数量写到第一条、其余删掉，保证后端与本地"一条"的形状一致
            client.updateInventoryItem(matched.first().id, ingredient)
                .getOrThrow()
            matched.drop(1).forEach { extra ->
                client.deleteInventoryItem(extra.id).onFailure { e ->
                    Log.w(TAG, "cleanup duplicate #${extra.id} failed: ${e.message}")
                }
            }
        }
    }

    /** 以 App 本地库存为权威，把云端完整收敛成同一份。 */
    suspend fun reconcileBackendToLocal(
        client: SfmBackendClient,
        localInventory: List<Ingredient>
    ): Result<InventoryReconcileResult> {
        if (!client.config.configured) {
            return Result.failure(IllegalStateException("尚未配置后端地址，请到设置中填写"))
        }
        return client.fetchInventoryRaw().mapCatching { remote ->
            val plan = planInventoryReconciliation(localInventory, remote)

            if (plan.add.isNotEmpty()) client.addInventoryItems(plan.add).getOrThrow()
            plan.update.forEach { (id, ingredient) ->
                client.updateInventoryItem(id, ingredient).getOrThrow()
            }
            plan.deleteIds.forEach { id -> client.deleteInventoryItem(id).getOrThrow() }

            InventoryReconcileResult(
                added = plan.add.size,
                updated = plan.update.size,
                deleted = plan.deleteIds.size,
            )
        }
    }

    /**
     * 从后端拉取库存，返回**本地还没有的**那些条目。
     *
     * 刻意只返回差集而不是整份覆盖：后端库存可能由网页调试工具或后端自动保存写入，
     * 拿它整份盖掉本地会把用户在 App 里刚删掉的东西又搬回来（离线删除尤其明显）。
     * 只补差集，合并方向就只有"补齐"，不会撤销任何本地操作。
     */
    suspend fun pullMissing(
        client: SfmBackendClient,
        localInventory: List<Ingredient>
    ): Result<List<Ingredient>> {
        if (!client.config.configured) {
            return Result.failure(IllegalStateException("尚未配置后端地址，请到设置中填写"))
        }
        return client.fetchInventory().map { remote ->
            remote.filter { item ->
                localInventory.none { local ->
                    sameInventoryIdentity(
                        local.name, local.quantityUnit,
                        item.name, item.quantityUnit
                    )
                }
            }
        }
    }

    /** 后端条目是否对应这个本地食材。 */
    private fun InventoryItemDto.matches(ingredient: Ingredient): Boolean =
        matchesInventoryIdentity(ingredient)
}

internal data class InventoryReconcilePlan(
    val add: List<Ingredient>,
    val update: List<Pair<Int, Ingredient>>,
    val deleteIds: List<Int>,
)

data class InventoryReconcileResult(
    val added: Int,
    val updated: Int,
    val deleted: Int,
)

internal fun planInventoryReconciliation(
    localInventory: List<Ingredient>,
    remoteInventory: List<InventoryItemDto>,
): InventoryReconcilePlan {
    val retainedRemoteIds = mutableSetOf<Int>()
    val add = mutableListOf<Ingredient>()
    val update = mutableListOf<Pair<Int, Ingredient>>()
    val duplicateIds = mutableListOf<Int>()

    localInventory.forEach { local ->
        val matches = remoteInventory.filter { it.matchesInventoryIdentity(local) }
        val retained = matches.firstOrNull()
        if (retained == null) {
            add += local
        } else {
            retainedRemoteIds += retained.id
            update += retained.id to local
            duplicateIds += matches.drop(1).map { it.id }
        }
    }

    val staleIds = remoteInventory
        .filter { it.id !in retainedRemoteIds && it.id !in duplicateIds }
        .map { it.id }
    return InventoryReconcilePlan(add, update, (duplicateIds + staleIds).distinct())
}

internal fun inventorySyncScope(baseUrl: String): String = baseUrl.trim().trimEnd('/')

internal fun shouldHydrateInventory(
    localInventoryEmpty: Boolean,
    currentScope: String,
    hydratedScopes: Set<String>
): Boolean = localInventoryEmpty && currentScope.isNotEmpty() && currentScope !in hydratedScopes

internal fun canReconcileInventory(
    localInventoryEmpty: Boolean,
    currentScope: String,
    hydratedScopes: Set<String>
): Boolean = currentScope.isNotEmpty() &&
    (!localInventoryEmpty || currentScope in hydratedScopes)

/**
 * 本次进入首页要不要跑一次全量对账。
 *
 * 判据是「欠不欠一次对账」，不是「有没有错误文案」。文案会被一次成功的新增清掉（对新增是对的），
 * 但删除同步失败在后端留下的残留只有全量对账能收敛。用文案当判据会让那种不一致永远等不到重试。
 */
internal fun shouldReconcileInventoryNow(
    reconciledScope: String,
    currentScope: String,
    needsReconcile: Boolean
): Boolean = currentScope.isNotEmpty() && (reconciledScope != currentScope || needsReconcile)

/**
 * 一次成功的批量新增之后，能不能把「未同步」的痕迹清干净。
 *
 * 只有在不欠全量对账时才可以。欠着对账却把痕迹清掉，等于对用户宣布已经同步好了，
 * 而后端仍有本地已删除的条目在参与配菜。
 */
internal fun mayClearSyncErrorAfterPush(needsReconcile: Boolean): Boolean = !needsReconcile

internal fun InventoryItemDto.matchesInventoryIdentity(ingredient: Ingredient): Boolean =
    sameInventoryIdentity(name, unit, ingredient.name, ingredient.quantityUnit)

internal fun sameInventoryIdentity(
    firstName: String,
    firstUnit: String,
    secondName: String,
    secondUnit: String
): Boolean = firstName.trim() == secondName.trim() &&
    normalizeInventoryUnit(firstUnit) == normalizeInventoryUnit(secondUnit)

private fun normalizeInventoryUnit(unit: String): String =
    unit.trim().let { if (it.lowercase() in setOf("g", "克")) "个" else it }
