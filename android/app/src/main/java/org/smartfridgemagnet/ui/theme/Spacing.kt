package org.smartfridgemagnet.ui.theme

import androidx.compose.ui.unit.dp

// 间距刻度：所有屏幕的 padding / 间隙统一引用这些 token，禁止随手写 dp 值。
object Spacing {
    val xs = 4.dp
    val sm = 8.dp
    val md = 12.dp
    val lg = 16.dp
    val xl = 20.dp
    val xxl = 24.dp
    val xxxl = 32.dp

    /** 悬浮 dock 会盖住内容，根屏的可滚动内容底部要留出这个空隙。 */
    val dockClearance = 96.dp
}
