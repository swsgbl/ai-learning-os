/**
 * Index.ets — 应用首页(M13-01 第 3c-5 小步 + M13-05 论文列表)
 *
 * 结构(刻意收窄):
 *   - 底部 Tabs(barPosition End):首页、学习、搜索、语音、设置;
 *   - 首页 Tab 展示 components/HomePane(六区只读面板,内部自带 Scroll 可滚动,
 *     aboutToAppear 读取已保存服务地址后整体刷新一次);
 *   - 学习 Tab 展示 components/StudyPane(GET /api/v1/papers 论文只读列表,
 *     带状态机和 URL 订阅刷新);
 *   - 搜索 Tab 展示 components/SearchPane(只读占位,不发起搜索/预览/回查请求,
 *     不发起任何 API 请求,内部自行铺满可用区域);
 *   - 语音 Tab 展示 components/VoicePane(只读占位,不访问麦克风,
 *     不启动语音识别或合成,不发起任何 API 请求,内部自行铺满可用区域);
 *   - 设置 Tab 展示 components/SettingsPane(地址编辑/保存/连接测试)。
 */

import { HomePane } from '../components/HomePane';
import { SearchPane } from '../components/SearchPane';
import { SettingsPane } from '../components/SettingsPane';
import { StudyPane } from '../components/StudyPane';
import { VoicePane } from '../components/VoicePane';

@Entry
@Component
struct Index {
  build() {
    Column() {
      Tabs({ barPosition: BarPosition.End }) {
        TabContent() {
          Column() {
            HomePane()
          }
          .width('100%')
          .height('100%')
        }
        .tabBar('首页')

        TabContent() {
          StudyPane()
        }
        .tabBar('学习')

        TabContent() {
          SearchPane()
        }
        .tabBar('搜索')

        TabContent() {
          VoicePane()
        }
        .tabBar('语音')

        TabContent() {
          Column() {
            SettingsPane()
          }
          .width('100%')
          .height('100%')
        }
        .tabBar('设置')
      }
      .scrollable(true)
      .barMode(BarMode.Fixed)
      .width('100%')
      .height('100%')
    }
    .width('100%')
    .height('100%')
  }
}
