# 图标设计源文件

本目录保存食材与分类图标的原始 SVG，以及食材中文名到 Android drawable 的权威映射。

```text
Food/           134 个食材 SVG
category/       分类 SVG
food_map.json   中文食材名 -> food_XXX
```

`food_map.json` 是唯一映射表，值与
`android/app/src/main/res/drawable/food_*.png` 一一对应。Android 资源名不能使用中文，运行时
通过该映射找到数字资源名。

新增食材图标时：

1. 把原始 SVG 放入 `Food/`。
2. 以现有尺寸和透明背景规则导出新的 `food_XXX.png` 到 Android drawable。
3. 在 `food_map.json` 增加一条映射，并检查 ID 不重复、文件存在。

App 使用 PNG 而不是 vector drawable，以保留多色渐变和细节。不要创建 `*_final`、`*_new`
等平行映射文件；在权威文件上原地更新。

## 来源记录

项目所有者确认本目录素材来自公开来源并允许进入公开快照，但当前没有逐文件保存原始页面、
作者和许可证。公开可访问不等于自动允许任意再分发，因此下游复用前应补齐逐素材来源与许可；
在记录完整前，不应仅凭根目录 GPL 声明推断这些第三方素材的授权范围。详见
[`NOTICE.md`](../NOTICE.md)。
