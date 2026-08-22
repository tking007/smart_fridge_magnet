package org.smartfridgemagnet.network

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 待对账状态机。
 *
 * 守的是一条静默不一致链：删除同步失败 → 留下警示 → 用户随后新增成功 → 警示被清掉 → 而后端那件
 * 早就吃完的食材还在参与餐前配菜。警示没了、作用域也已标记对账完成，这个不一致从此没有出口。
 */
class PendingInventoryReconcileTest {

    // ── 一次成功的新增之后，能不能清掉「未同步」痕迹 ──

    @Test
    fun `不欠对账时新增成功可以清掉痕迹`() {
        assertTrue(mayClearSyncErrorAfterPush(needsReconcile = false))
    }

    @Test
    fun `欠着对账时新增成功不得清掉痕迹`() {
        // 这一批推的是本地全部食材，失败的新增确实补上了；但后端多出来的条目
        // （删除同步失败留下的）再怎么新增也去不掉。
        assertFalse(mayClearSyncErrorAfterPush(needsReconcile = true))
    }

    // ── 进首页时要不要跑对账 ──

    @Test
    fun `本作用域没对账过时要跑`() {
        assertTrue(
            shouldReconcileInventoryNow(
                reconciledScope = "",
                currentScope = "https://api.example.com",
                needsReconcile = false,
            )
        )
    }

    @Test
    fun `已对账且不欠时跳过`() {
        assertFalse(
            shouldReconcileInventoryNow(
                reconciledScope = "https://api.example.com",
                currentScope = "https://api.example.com",
                needsReconcile = false,
            )
        )
    }

    @Test
    fun `已对账但欠着一次时仍要跑`() {
        // 正是删除同步失败后的处境：作用域已标记对账完成，若不看这个标记就永不重试。
        assertTrue(
            shouldReconcileInventoryNow(
                reconciledScope = "https://api.example.com",
                currentScope = "https://api.example.com",
                needsReconcile = true,
            )
        )
    }

    @Test
    fun `换了后端地址要重新对账`() {
        assertTrue(
            shouldReconcileInventoryNow(
                reconciledScope = "https://old.example.com",
                currentScope = "https://new.example.com",
                needsReconcile = false,
            )
        )
    }

    @Test
    fun `没配后端时不跑`() {
        assertFalse(
            shouldReconcileInventoryNow(
                reconciledScope = "",
                currentScope = "",
                needsReconcile = true,
            )
        )
    }

    // ── 整条链路的顺序回归 ──

    @Test
    fun `删除失败后再新增成功不会掩盖不一致`() {
        var needsReconcile = false
        val scope = "https://api.example.com"
        var reconciledScope = scope   // 启动时已对账过

        // 1) 删除同步失败 → 标记欠一次对账
        needsReconcile = true

        // 2) 用户随后新增一样食材，推送成功
        val clearedByPush = mayClearSyncErrorAfterPush(needsReconcile)
        assertFalse("新增成功不得清掉删除留下的不一致", clearedByPush)

        // 3) 因此仍然会去跑对账（而不是等到下一次冷启动）
        assertTrue(shouldReconcileInventoryNow(reconciledScope, scope, needsReconcile))

        // 4) 对账成功后才清标记，此后不再重复跑
        needsReconcile = false
        reconciledScope = scope
        assertFalse(shouldReconcileInventoryNow(reconciledScope, scope, needsReconcile))
    }
}
