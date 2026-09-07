/**
 * StudyPane.ets — 学习 Tab 只读占位面板(M13-01 第 3c-3 小步)
 *
 * 功能(刻意收窄):
 *   - 纯静态只读占位:标题「学习」+「只读占位，功能未实现」+ 范围说明
 *     「当前不加载课程、试卷和学习记录」;
 *   - 不发起任何 API 请求,不保存任何状态,不访问麦克风/存储,不引入权限;
 *   - 无 @State、无生命周期逻辑、无任何 import 依赖,内部自行铺满可用区域;
 *   - 结构简洁可复用:后续接线学习功能时以此为骨架替换内部实现。
 */

@Component
export struct StudyPane {
  build() {
    Column({ space: 12 }) {
      Text('学习')
        .fontSize(20)
        .fontWeight(FontWeight.Bold)

      Text('只读占位，功能未实现')
        .fontSize(16)
        .fontColor('#c62828')

      Text('当前不加载课程、试卷和学习记录')
        .fontSize(13)
        .fontColor('#666666')
    }
    .justifyContent(FlexAlign.Center)
    .alignItems(HorizontalAlign.Center)
    .width('100%')
    .height('100%')
  }
}
