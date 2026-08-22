package org.smartfridgemagnet.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// primary 是炭黑而非品牌绿：主按钮与悬浮加号吃掉这个槽位，绿色作为品牌强调色放在
// secondary 与 accents.brand。background 与 surface 必须不同色阶，卡片才有层次，
// 因此卡片不描边。
private val LightColors = lightColorScheme(
    primary = Charcoal,
    onPrimary = Neutral0,
    primaryContainer = Neutral100,
    onPrimaryContainer = Charcoal,
    secondary = Leaf,
    onSecondary = Neutral0,
    secondaryContainer = LeafSoft,
    onSecondaryContainer = LeafDeep,
    tertiary = Blue,
    onTertiary = Neutral0,
    tertiaryContainer = BlueSoft,
    onTertiaryContainer = BlueDeep,
    background = Neutral50,
    onBackground = Charcoal,
    surface = Neutral0,
    onSurface = Charcoal,
    surfaceVariant = Neutral100,
    onSurfaceVariant = Neutral500,
    surfaceContainer = Neutral50,
    surfaceContainerHigh = Neutral100,
    // 裸 M3 Card 的默认底色取自 surfaceContainerLow/Lowest。不显式给出时它们会被推导成
    // 另一个色阶，同一屏上就出现「AppCard 是白的、裸 Card 是灰的」两种卡片。
    surfaceContainerLow = Neutral0,
    surfaceContainerLowest = Neutral0,
    outline = Neutral400,
    outlineVariant = Neutral100,
    error = Red,
    onError = Neutral0,
    errorContainer = RedSoft,
    onErrorContainer = RedDeep,
    inverseSurface = Charcoal,
    inverseOnSurface = Neutral0,
)

private val DarkColors = darkColorScheme(
    primary = Neutral0,
    onPrimary = Charcoal,
    primaryContainer = Neutral800,
    onPrimaryContainer = Neutral0,
    secondary = LeafBright,
    onSecondary = LeafDeep,
    secondaryContainer = LeafDeep,
    onSecondaryContainer = LeafSoft,
    tertiary = Blue,
    onTertiary = Neutral900,
    tertiaryContainer = BlueDeep,
    onTertiaryContainer = BlueSoft,
    background = Neutral900,
    onBackground = Neutral0,
    surface = Neutral800,
    onSurface = Neutral0,
    surfaceVariant = Neutral800,
    onSurfaceVariant = Neutral400,
    surfaceContainer = Neutral800,
    surfaceContainerHigh = Neutral700,
    surfaceContainerLow = Neutral800,
    surfaceContainerLowest = Neutral900,
    outline = Neutral700,
    outlineVariant = Neutral700,
    error = RedBright,
    onError = Neutral900,
    errorContainer = RedDeep,
    onErrorContainer = RedSoft,
    inverseSurface = Neutral0,
    inverseOnSurface = Charcoal,
)

private val LightAccents = AppAccents(
    brand = AccentColor(Leaf, LeafSoft, LeafDeep),
    success = AccentColor(Leaf, LeafSoft, LeafDeep),
    warning = AccentColor(Amber, AmberSoft, AmberDeep),
    info = AccentColor(Blue, BlueSoft, BlueDeep),
    danger = AccentColor(Red, RedSoft, RedDeep),
    track = Neutral100,
    categories = listOf(
        AccentColor(Leaf, LeafSoft, LeafDeep),
        AccentColor(Amber, AmberSoft, AmberDeep),
        AccentColor(Rose, RoseSoft, RoseDeep),
        AccentColor(Blue, BlueSoft, BlueDeep),
        AccentColor(Violet, VioletSoft, VioletDeep),
        AccentColor(Teal, TealSoft, TealDeep),
        AccentColor(Sun, SunSoft, SunDeep),
    ),
)

private val DarkAccents = AppAccents(
    brand = AccentColor(LeafBright, LeafDeep, LeafSoft),
    success = AccentColor(LeafBright, LeafDeep, LeafSoft),
    warning = AccentColor(Amber, AmberDeep, AmberSoft),
    info = AccentColor(Blue, BlueDeep, BlueSoft),
    danger = AccentColor(RedBright, RedDeep, RedSoft),
    track = Neutral700,
    categories = listOf(
        AccentColor(LeafBright, LeafDeep, LeafSoft),
        AccentColor(Amber, AmberDeep, AmberSoft),
        AccentColor(Rose, RoseDeep, RoseSoft),
        AccentColor(Blue, BlueDeep, BlueSoft),
        AccentColor(Violet, VioletDeep, VioletSoft),
        AccentColor(Teal, TealDeep, TealSoft),
        AccentColor(Sun, SunDeep, SunSoft),
    ),
)

/**
 * 全应用主题入口：注入配色 / 字阶 / 圆角 / 语义色，并把状态栏染成页面底色。
 * 所有屏幕通过 MaterialTheme.* 与 MaterialTheme.accents 读取，不直接引用原始色值。
 */
@Composable
fun AppTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colors = if (darkTheme) DarkColors else LightColors
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colors.background.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }
    CompositionLocalProvider(LocalAccents provides if (darkTheme) DarkAccents else LightAccents) {
        MaterialTheme(
            colorScheme = colors,
            typography = AppTypography,
            shapes = AppShapes,
            content = content,
        )
    }
}
