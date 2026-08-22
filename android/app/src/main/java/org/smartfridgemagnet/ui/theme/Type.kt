package org.smartfridgemagnet.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// 字阶。层级靠字号 + 字重同时拉开：标题够大够粗，说明文字降到灰色小字，
// 数据用 displaySmall 的等宽数字当主角。业务代码只引用 MaterialTheme.typography.*。
private val Default = FontFamily.Default

val AppTypography = Typography(
    // 页面主标题（问候语、结论式双行标题）
    displayLarge = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Bold, fontSize = 34.sp, lineHeight = 40.sp,
        letterSpacing = (-0.6).sp,
    ),
    displayMedium = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Bold, fontSize = 28.sp, lineHeight = 34.sp,
        letterSpacing = (-0.5).sp,
    ),
    // 大数字：等宽数位，避免数值变化时左右跳动
    displaySmall = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Bold, fontSize = 30.sp, lineHeight = 34.sp,
        letterSpacing = (-1).sp, fontFeatureSettings = "tnum",
    ),
    headlineLarge = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Bold, fontSize = 22.sp, lineHeight = 28.sp,
        fontFeatureSettings = "tnum",
    ),
    // 分区标题
    headlineMedium = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Bold, fontSize = 19.sp, lineHeight = 25.sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Bold, fontSize = 17.sp, lineHeight = 23.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.SemiBold, fontSize = 17.sp, lineHeight = 23.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 22.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Medium, fontSize = 15.sp, lineHeight = 21.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Normal, fontSize = 16.sp, lineHeight = 24.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Normal, fontSize = 14.sp, lineHeight = 20.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Normal, fontSize = 13.sp, lineHeight = 18.sp,
    ),
    // 按钮文字
    labelLarge = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 20.sp,
    ),
    // 大数字上方的小灰标签
    labelMedium = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Medium, fontSize = 12.sp, lineHeight = 16.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = Default, fontWeight = FontWeight.Medium, fontSize = 11.sp, lineHeight = 15.sp,
    ),
)
