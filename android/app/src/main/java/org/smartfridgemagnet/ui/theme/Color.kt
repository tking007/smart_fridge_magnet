package org.smartfridgemagnet.ui.theme

import androidx.compose.ui.graphics.Color

// 设计系统调色板。语义映射在 Theme.kt，业务代码只引用 MaterialTheme.colorScheme.* 与
// MaterialTheme.accents.*，不直接用这里的原始色值。

// 深色件：主按钮、悬浮加号、确认键。带一点绿调的炭黑，比纯黑柔和。
val Charcoal = Color(0xFF3B3C39)
val CharcoalSoft = Color(0xFF55574F)

// 品牌绿（柠檬叶绿）：激活态、进度、成功、新鲜。
val Leaf = Color(0xFF8DBE49)
val LeafBright = Color(0xFFA7D65E)
val LeafSoft = Color(0xFFEAF4DC)
val LeafDeep = Color(0xFF3F5D19)

// 中性：页面底色与卡片必须是**两个不同色阶**，层次靠色差而非边框。
val Neutral0 = Color(0xFFFFFFFF)   // 卡片（浅色）
val Neutral50 = Color(0xFFF6F6F6)  // 页面底色（浅色）
val Neutral100 = Color(0xFFEDEDEB) // 轨道 / 分段底 / 图标底板
val Neutral200 = Color(0xFFE0E0DD)
val Neutral400 = Color(0xFFB4B4AF)
val Neutral500 = Color(0xFF8E8E88)  // 次要文字
val Neutral700 = Color(0xFF4A4A45)
val Neutral800 = Color(0xFF2C2D2A)  // 卡片（深色）
val Neutral900 = Color(0xFF1D1E1B)  // 页面底色（深色）

// 分类色板：每个食材类别一个颜色，让列表一眼分得开类别。
val Blue = Color(0xFF4FA9F3)
val BlueSoft = Color(0xFFE2F2FF)
val BlueDeep = Color(0xFF0B3D66)

val Amber = Color(0xFFFFA83C)
val AmberSoft = Color(0xFFFFF0E0)
val AmberDeep = Color(0xFF5C3300)

val Rose = Color(0xFFFF6B8F)
val RoseSoft = Color(0xFFFFE8EE)
val RoseDeep = Color(0xFF6B1027)

val Violet = Color(0xFF9C86F5)
val VioletSoft = Color(0xFFEFEBFF)
val VioletDeep = Color(0xFF2E1C6B)

val Teal = Color(0xFF2FC4A8)
val TealSoft = Color(0xFFDDF7F1)
val TealDeep = Color(0xFF0A4A3D)

val Sun = Color(0xFFF5C63C)
val SunSoft = Color(0xFFFFF6D9)
val SunDeep = Color(0xFF5A4300)

// 危险 / 失败。
val Red = Color(0xFFEF4A4A)
val RedBright = Color(0xFFFF7A7A)
val RedSoft = Color(0xFFFFE7E7)
val RedDeep = Color(0xFF5C0F0F)
