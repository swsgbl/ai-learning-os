/**
 * VoiceExamViews.ets — 语音作答页的选卷视图与报告视图(M14-277)
 *
 * 从 VoiceExamPage 拆出(单文件 ≤400 行约束):
 *   - VoicePickPane:试卷选择 + 加载/错误态(经回调上抛所选 paperId);
 *   - VoiceReportPane:完卷报告摘要 + 重试/再考回调;
 * 两视图均为纯展示组件(状态由父页面持有,动作经回调上抛),
 * 不持有任何网络/录音资源。
 */

import { PaperOut } from '../AiosApi';
import { VoiceReportOut } from './VoiceExamApi';

/** 当前题卡片视图(题目 + 选项 + 进度/状态,只读展示) */
@Component
export struct QuestionPane {
  @Prop stem: string = '';
  @Prop optionsText: string = '';
  @Prop index: number = 0;
  @Prop total: number = 0;
  @Prop status: string = '';
  @Prop committed: string = '';
  build() {
    Column({ space: 12 }) {
      Column({ space: 6 }) {
        Text(`第 ${this.index + 1}/${this.total} 题 · 会话状态: ${this.status}`)
          .fontSize(12)
          .fontColor('#666666')
        Text(this.stem)
          .fontSize(16)
          .fontWeight(FontWeight.Medium)
        if (this.optionsText.length > 0) {
          Text(this.optionsText)
            .fontSize(13)
            .fontColor('#444444')
        }
      }
      .width('100%')
      .padding(12)
      .backgroundColor('#fafafa')
      .borderRadius(8)
      .alignItems(HorizontalAlign.Start)
      if (this.committed.length > 0) {
        Text(`已提交答案: ${this.committed}`)
          .fontSize(12)
          .fontColor('#2e7d32')
          .maxLines(2)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
      }
    }
    .width('100%')
  }
}

/** 试卷选择视图(纯展示 + 回调) */
@Component
export struct VoicePickPane {
  @Prop papers: PaperOut[] = [];
  @Prop loading: boolean = false;
  @Prop errorText: string = '';
  /** 用户点选某试卷 */
  onPick: (paperId: string) => void = () => {
  };
  /** 点重试(重载试卷列表) */
  onRetry: () => void = () => {
  };

  build() {
    Column({ space: 12 }) {
      Text('选择试卷开始语音作答')
        .fontSize(14)
        .fontColor('#666666')
      if (this.loading) {
        Text('加载试卷列表...').fontSize(14).fontColor('#1890ff')
      }
      if (this.errorText.length > 0) {
        Text(this.errorText).fontSize(13).fontColor('#c62828')
        Button('重试')
          .fontSize(13)
          .height(32)
          .onClick(() => {
            this.onRetry();
          })
      }
      Scroll() {
        Column({ space: 8 }) {
          ForEach(this.papers, (p: PaperOut) => {
            Button(p.title)
              .fontSize(14)
              .width('100%')
              .align(Alignment.Start)
              .onClick(() => {
                this.onPick(p.id);
              })
          }, (p: PaperOut) => p.id)
        }
        .width('100%')
      }
      .layoutWeight(1)
    }
    .width('100%')
    .height('100%')
  }
}

/** 完卷报告视图(纯展示 + 回调) */
@Component
export struct VoiceReportPane {
  @Link report: VoiceReportOut | null;
  @Prop busyHint: string = '';
  @Prop errorText: string = '';
  onRetry: () => void = () => {
  };
  onNewExam: () => void = () => {
  };

  build() {
    Scroll() {
      Column({ space: 12 }) {
        Text('完卷报告')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
        if (this.busyHint.length > 0) {
          Text(this.busyHint).fontSize(13).fontColor('#1890ff')
        }
        if (this.errorText.length > 0) {
          Text(this.errorText).fontSize(13).fontColor('#c62828')
          Button('重试')
            .fontSize(13)
            .height(32)
            .onClick(() => {
              this.onRetry();
            })
        }
        if (this.report !== null) {
          Column({ space: 6 }) {
            Text(`会话: ${this.report.session_id}`)
              .fontSize(11)
              .fontColor('#999999')
            Text('作答文本:')
              .fontSize(13)
              .fontColor('#666666')
            Text(this.report.spoken_text)
              .fontSize(14)
              .padding(8)
              .backgroundColor('#f5f5f5')
              .borderRadius(6)
              .width('100%')
            Text(`失误摘要: ${this.report.mistake_summary.length} 条 · 补救建议: ${this.report.remediation_summary.length} 条`)
              .fontSize(12)
              .fontColor('#666666')
          }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
        }
        Button('再考一卷')
          .fontSize(14)
          .margin({ top: 12 })
          .onClick(() => {
            this.onNewExam();
          })
      }
      .width('100%')
    }
    .layoutWeight(1)
  }
}


/** 作答 utterance 视图:转写草稿 + 录音/识别/提交操作区(纯展示 + 回调) */
@Component
export struct UtterancePane {
  @Prop draft: string = '';
  /** 0=IDLE 1=RECORDING 2=TRANSCRIBING 3=DRAFT 4=SUBMITTING(与页面 TurnState 一一对应) */
  @Prop turnState: number = 0;
  onStartRecord: () => void = () => {
  };
  onStopAndTranscribe: () => void = () => {
  };
  onDiscardDraft: () => void = () => {
  };
  onSubmit: () => void = () => {
  };

  build() {
    Column({ space: 10 }) {
      if (this.turnState === 3 || this.turnState === 4) {
        Column({ space: 8 }) {
          Text('识别结果(点击提交作为本题答案):')
            .fontSize(13)
            .fontColor('#666666')
          Text(this.draft)
            .fontSize(15)
            .padding(8)
            .backgroundColor('#f5f5f5')
            .borderRadius(6)
            .width('100%')
        }
        .width('100%')
        .alignItems(HorizontalAlign.Start)
      }
      Row({ space: 10 }) {
        if (this.turnState === 0) {
          Button('开始作答')
            .fontSize(15)
            .onClick(() => {
              this.onStartRecord();
            })
        }
        if (this.turnState === 1) {
          Button('停止并识别')
            .fontSize(15)
            .backgroundColor('#c62828')
            .onClick(() => {
              this.onStopAndTranscribe();
            })
        }
        if (this.turnState === 3) {
          Button('重录')
            .fontSize(14)
            .backgroundColor('#888888')
            .onClick(() => {
              this.onDiscardDraft();
            })
        }
        if (this.turnState === 3) {
          Button('提交答案')
            .fontSize(15)
            .onClick(() => {
              this.onSubmit();
            })
        }
      }
      .width('100%')
      .justifyContent(FlexAlign.Center)
      if (this.turnState === 1) {
        Text('● 录音中...')
          .fontSize(13)
          .fontColor('#c62828')
      }
    }
    .width('100%')
  }
}

/**
 * VoiceCommandPane — FSM 命令按钮区(hm-parity v5 · 按钮显隐矩阵逐条对齐
 * Android VoiceScreen.kt AdvanceControls 449-493 行):
 *   SESSION_READY   →「开始读题」(start_reading)
 *   ANSWER_COMMITTED→「确认，进入下一题」(commit_confirmed)
 *   NEXT_QUESTION   →「读下一题」(start_reading)+「完成全卷」(report_ready)
 *   REPORT_READY    →「生成报告」(requestReport)
 *   WAITING_ANSWER / CLARIFYING →「跳过本题」(skip)
 *   非终态恒显「结束语音陪练」(end,红字)——与 Android TextButton 一致
 * 纯展示组件:动作经回调上抛,状态由父页面持有。
 */
@Component
export struct VoiceCommandPane {
  /** 服务端 FSM 会话状态(8 值之一) */
  @Prop status: string = '';
  @Prop busy: boolean = false;
  onStartReading: () => void = () => {
  };
  onCommitConfirmed: () => void = () => {
  };
  onReportReady: () => void = () => {
  };
  onSkip: () => void = () => {
  };
  onEnd: () => void = () => {
  };
  onRequestReport: () => void = () => {
  };

  build() {
    Column({ space: 10 }) {
      if (this.status === 'SESSION_READY') {
        Button('开始读题')
          .fontSize(15)
          .width('100%')
          .enabled(!this.busy)
          .onClick(() => {
            this.onStartReading();
          })
      }
      if (this.status === 'ANSWER_COMMITTED') {
        Button('确认，进入下一题')
          .fontSize(15)
          .width('100%')
          .enabled(!this.busy)
          .onClick(() => {
            this.onCommitConfirmed();
          })
      }
      if (this.status === 'NEXT_QUESTION') {
        Row({ space: 10 }) {
          Button('读下一题')
            .fontSize(14)
            .layoutWeight(1)
            .enabled(!this.busy)
            .onClick(() => {
              this.onStartReading();
            })
          Button('完成全卷')
            .fontSize(14)
            .backgroundColor('#2e7d32')
            .layoutWeight(1)
            .enabled(!this.busy)
            .onClick(() => {
              this.onReportReady();
            })
        }
        .width('100%')
      }
      if (this.status === 'REPORT_READY') {
        Button(this.busy ? '正在生成…' : '生成报告')
          .fontSize(15)
          .width('100%')
          .backgroundColor('#722ed1')
          .enabled(!this.busy)
          .onClick(() => {
            this.onRequestReport();
          })
      }
      if (this.status === 'WAITING_ANSWER' || this.status === 'CLARIFYING') {
        Button('跳过本题')
          .fontSize(14)
          .width('100%')
          .backgroundColor('#f0f0f0')
          .fontColor('#333333')
          .enabled(!this.busy)
          .onClick(() => {
            this.onSkip();
          })
      }
      if (this.status !== 'REPORT_READY') {
        Text('结束语音陪练')
          .fontSize(13)
          .fontColor('#c62828')
          .width('100%')
          .textAlign(TextAlign.Center)
          .padding(6)
          .onClick(() => {
            this.onEnd();
          })
      }
    }
    .width('100%')
  }
}

/**
 * VoiceResumeCard — 「继续上次语音作答」恢复入口(hm-parity v5 · 对齐
 * Android 恢复槽交互:本地槽只是提示,点击走 GET resume 对齐服务端状态,
 * 服务端是唯一真相)。纯展示组件,动作经回调上抛。
 */
@Component
export struct VoiceResumeCard {
  @Prop sessionId: string = '';
  @Prop busy: boolean = false;
  onResume: () => void = () => {
  };
  onDismiss: () => void = () => {
  };

  build() {
    Column({ space: 8 }) {
      Row({ space: 8 }) {
        Text('⏯ 继续上次语音作答')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .fontColor('#1890ff')
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Button(this.busy ? '恢复中…' : '继续')
          .fontSize(13)
          .height(32)
          .backgroundColor('#1890ff')
          .enabled(!this.busy)
          .onClick(() => {
            this.onResume();
          })
      }
      .width('100%')
      Text(`会话 ${this.sessionId}(以服务端状态为准)`)
        .fontSize(11)
        .fontColor('#888888')
        .width('100%')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
      Text('放弃恢复')
        .fontSize(11)
        .fontColor('#999999')
        .width('100%')
        .textAlign(TextAlign.End)
        .onClick(() => {
          this.onDismiss();
        })
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#e6f7ff')
    .borderRadius(8)
  }
}
