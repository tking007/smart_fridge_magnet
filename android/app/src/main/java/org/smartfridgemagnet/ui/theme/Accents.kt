package org.smartfridgemagnet.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/** 一组同语义的配色：填充色 + 容器底色 + 容器上的文字色。 */
@Immutable
data class AccentColor(
    val solid: Color,
    val container: Color,
    val onContainer: Color,
)

/**
 * 语义色与分类色。
 *
 * Material3 没有 success / warning 槽位，而 primary 已被炭黑主操作占用，所以这些
 * 单独成一组。[categories] 用于让每个食材类别有自己的颜色 —— 全灰的列表让人分不出
 * 哪几行是同一类。
 */
@Immutable
data class AppAccents(
    /** 品牌绿：激活态、进度、强调。 */
    val brand: AccentColor,
    val success: AccentColor,
    val warning: AccentColor,
    val info: AccentColor,
    val danger: AccentColor,
    /** 轨道底：进度条、分段控件、图标底板。 */
    val track: Color,
    /** 分类色板，按 `ordinal % size` 取用即可保证同类同色。 */
    val categories: List<AccentColor>,
)

val LocalAccents = staticCompositionLocalOf<AppAccents> {
    error("AppAccents 未提供，屏幕必须包在 AppTheme 里")
}

val MaterialTheme.accents: AppAccents
    @Composable @ReadOnlyComposable get() = LocalAccents.current

/** 按稳定序号取一个分类色，同一类别在任何屏幕上都是同一个颜色。 */
@Composable
@ReadOnlyComposable
fun categoryAccent(ordinal: Int): AccentColor {
    val palette = LocalAccents.current.categories
    return palette[((ordinal % palette.size) + palette.size) % palette.size]
}
