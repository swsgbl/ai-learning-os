/**
 * Index.ets — 应用首页(M13-01 第 3c-5 小步 + M13-05 论文列表 + M13-11 治理 Tab)
 *
 * 结构(刻意收窄):
 *   - 底部 Tabs(barPosition End):首页、学习、搜索、语音、设置、治理;
 *   - 首页 Tab 展示 components/HomePane(六区只读面板,内部自带 Scroll 可滚动,
 *     aboutToAppear 读取已保存服务地址后整体刷新一次);
 *   - 学习 Tab 展示 components/StudyPane(GET /api/v1/papers 论文只读列表 +
 *     GET /api/v1/exams/{exam_id} 考试会话只读快照,带状态机和 URL 订阅刷新);
 *   - 搜索 Tab 展示 components/SearchPane(GET /api/v1/search/providers 搜索 providers 只读列表,
 *     带状态机和 URL 订阅刷新);
 *   - 语音 Tab 展示 components/VoicePane(只读占位,不访问麦克风,
 *     不启动语音识别或合成,不发起任何 API 请求,内部自行铺满可用区域);
 *   - 设置 Tab 展示 components/SettingsPane(地址编辑/保存/连接测试);
 *   - 治理 Tab 展示 components/GovernancePane(M13-11,版本/运维快照/审计日志
 *     三区只读,仅 getVersion/getOpsSnapshot/getAudit 三个 GET,带状态机与
 *     URL 订阅刷新;既有五 Tab 行为保持不变)。
 */

import { GovernancePane } from '../components/GovernancePane';
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

        TabContent() {
          Column() {
            GovernancePane()
          }
          .width('100%')
          .height('100%')
        }
        .tabBar('治理')
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
