package org.smartfridgemagnet.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

// 圆角规格。卡片用 large(22dp) 的大圆角是本设计系统的视觉特征；
// 主按钮走 Pill 而非 Shapes 槽位，因为它与容器圆角无关，永远是半高。
val AppShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    // 20dp 而非 16：裸 M3 Card 用的是 medium，与 AppCard 的 large(22dp) 拉开太多时
    // 同一屏上两种卡片的圆角肉眼可辨。
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(22.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

/** 胶囊：主按钮、徽标、分段控件。 */
val Pill = RoundedCornerShape(percent = 50)

/** 底部抽屉：只有上方两角是圆角。 */
val SheetShape = RoundedCornerShape(topStart = 26.dp, topEnd = 26.dp)
