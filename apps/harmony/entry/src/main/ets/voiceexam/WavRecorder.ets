/**
 * WavRecorder.ets — 语音作答录音控制器(M14-277)
 *
 * 设计(任务书契约):
 *   - AudioCapturer 原始 PCM 采集(16000 Hz / 单声道 / S16LE)而非 AVRecorder:
 *     AVRecorder 无 PCM 编码(AAC/MP3/G711MU/AMR 系),产不出服务端 FunASR
 *     要求的 16bit PCM WAV;AudioCapturer 采集的原始 S16LE 流 + 手写 44 字节
 *     RIFF 头 = 精确的 16k/mono/16bit WAV;
 *   - 单次录音 = 一段作答 utterance:start() → 采集循环(2KB 块)→
 *     stop() 返回完整 WAV Uint8Array;
 *   - 内存态组装,不写任何文件(隐私对齐 PRIVACY_STORE_AUDIO:用完即弃,
 *     音频字节只在本次转写上传中使用一次,绝不落盘);
 *   - 麦克风权限:abilityAccessCtrl requestPermissionsFromUser 运行时申请;
 *     拒绝 = 如实返回不可用(错误原样上抛,调用方展示不可用态,绝不静默重试);
 *   - 采集失败(设备占用/系统拒绝等)如实抛错,不返回空音频冒充成功。
 */

import { audio } from '@kit.AudioKit';
import { abilityAccessCtrl, common, Permissions } from '@kit.AbilityKit';
import { safeErrorText } from '../ErrorText';

/** 权限申请结果的最小投影(authResults 与 permissions 按序对齐,0 = 已授权) */
interface MicPermissionResult {
  authResults: Array<number>;
}

/** 服务端 FunASR 契约的采集参数(16k/mono/16bit 固定,不做成可配置) */
const SAMPLE_RATE: number = 16000;
const CHANNELS: number = 1;
const BITS_PER_SAMPLE: number = 16;
/** 单次 read 块大小(字节):2KB 对齐 S16LE 帧,阻塞读低延迟 */
const READ_CHUNK_BYTES: number = 2048;
/** 采集循环空转保护:连续空读上限(约 5s 无数据视为设备异常) */
const MAX_EMPTY_READS: number = 250;

/**
 * 请求麦克风权限(requestPermissionsFromUser 运行时申请)。
 * 返回 '' 表示已授权;否则返回不可用原因(调用方如实展示,不重试)。
 */
export async function requestMicPermission(context: common.Context): Promise<string> {
  const permissions: Array<Permissions> = ['ohos.permission.MICROPHONE'];
  try {
    const atManager: abilityAccessCtrl.AtManager = abilityAccessCtrl.createAtManager();
    const result: MicPermissionResult =
      await atManager.requestPermissionsFromUser(context, permissions) as MicPermissionResult;
    // authResults 与 permissions 按序对齐;0 = 已授权
    if (result.authResults.length > 0 && result.authResults[0] === 0) {
      return '';
    }
    return '麦克风权限未授予:语音作答不可用(请在系统设置中授权后重试)';
  } catch (e) {
    return `麦克风权限申请失败: ${safeErrorText(e)}`;
  }
}

/**
 * 构造 44 字节标准 WAV 头(RIFF little-endian,PCM s16le)。
 * dataLen: PCM 数据字节数。
 */
function buildWavHeader(dataLen: number): Uint8Array {
  const header: Uint8Array = new Uint8Array(44);
  const dv: DataView = new DataView(header.buffer);
  const writeAscii = (offset: number, text: string): void => {
    for (let i: number = 0; i < text.length; i++) {
      dv.setUint8(offset + i, text.charCodeAt(i));
    }
  };
  const byteRate: number = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE / 8;
  const blockAlign: number = CHANNELS * BITS_PER_SAMPLE / 8;
  writeAscii(0, 'RIFF');
  dv.setUint32(4, 36 + dataLen, true);
  writeAscii(8, 'WAVE');
  writeAscii(12, 'fmt ');
  dv.setUint32(16, 16, true);        // fmt chunk size(PCM 固定 16)
  dv.setUint16(20, 1, true);         // audio format: PCM
  dv.setUint16(22, CHANNELS, true);
  dv.setUint32(24, SAMPLE_RATE, true);
  dv.setUint32(28, byteRate, true);
  dv.setUint16(32, blockAlign, true);
  dv.setUint16(34, BITS_PER_SAMPLE, true);
  writeAscii(36, 'data');
  dv.setUint32(40, dataLen, true);
  return header;
}

/**
 * 单次录音会话:start() 开始采集,stop() 结束并返回完整 WAV 字节。
 * 一次 WavRecorderSession 只能录一段(utterance 语义);重复 start 抛错。
 */
export class WavRecorderSession {
  private capturer: audio.AudioCapturer | null = null;
  private pcmChunks: Array<Uint8Array> = [];
  private pcmTotal: number = 0;
  private running: boolean = false;
  private loopError: string | null = null;

  /** 当前是否在采集中 */
  isRunning(): boolean {
    return this.running;
  }

  /**
   * 开始采集。设备创建/启动失败如实抛错(Error.message 已净化)。
   */
  async start(): Promise<void> {
    if (this.running) {
      throw new Error('录音已在进行中');
    }
    const opts: audio.AudioCapturerOptions = {
      streamInfo: {
        samplingRate: audio.AudioSamplingRate.SAMPLE_RATE_16000,
        channels: audio.AudioChannel.CHANNEL_1,
        sampleFormat: audio.AudioSampleFormat.SAMPLE_FORMAT_S16LE,
        encodingType: audio.AudioEncodingType.ENCODING_TYPE_RAW
      },
      capturerInfo: {
        source: audio.SourceType.SOURCE_TYPE_MIC,
        capturerFlags: 0
      }
    };
    try {
      this.capturer = await audio.createAudioCapturer(opts);
      await this.capturer.start();
    } catch (e) {
      // 创建/启动失败:释放半成品,如实抛错
      if (this.capturer !== null) {
        try {
          await this.capturer.release();
        } catch (ignore) {
          // 释放失败不掩盖原始错误
        }
      }
      this.capturer = null;
      throw new Error(`无法启动录音: ${safeErrorText(e)}`);
    }
    this.pcmChunks = [];
    this.pcmTotal = 0;
    this.running = true;
    // 后台采集循环(不 await:循环自行推进,stop() 负责收尾)
    this.collectLoop().catch((e: Error) => {
      this.loopError = `录音中断: ${e.message}`;
      this.running = false;
    });
  }

  /** 采集循环:持续 read 直到 running=false / 设备异常 / 空读超限 */
  private async collectLoop(): Promise<void> {
    const capturer: audio.AudioCapturer | null = this.capturer;
    if (capturer === null) {
      return;
    }
    let emptyReads: number = 0;
    while (this.running) {
      let chunk: ArrayBuffer;
      try {
        chunk = await capturer.read(READ_CHUNK_BYTES, true);
      } catch (e) {
        throw new Error(safeErrorText(e));
      }
      if (chunk.byteLength === 0) {
        emptyReads++;
        if (emptyReads > MAX_EMPTY_READS) {
          throw new Error('录音设备持续无数据');
        }
        continue;
      }
      emptyReads = 0;
      this.pcmChunks.push(new Uint8Array(chunk));
      this.pcmTotal += chunk.byteLength;
    }
  }

  /**
   * 停止采集并返回完整 WAV(44 字节头 + PCM 数据)。
   * 循环中的异常(设备中断)在此如实抛出;零数据(过短)同样如实抛错,
   * 不返回只有头的空 WAV 冒充成功。
   */
  async stop(): Promise<Uint8Array> {
    if (!this.running) {
      throw new Error('录音未在进行中');
    }
    this.running = false;
    const capturer: audio.AudioCapturer | null = this.capturer;
    // 给采集循环最后一个 read 返回的窗口
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 80);
    });
    if (capturer !== null) {
      try {
        await capturer.stop();
      } catch (ignore) {
        // stop 失败不阻断:数据已在内存,release 兜底回收设备
      }
      try {
        await capturer.release();
      } catch (ignore) {
        // release 失败不阻断:设备由系统最终回收
      }
    }
    this.capturer = null;
    if (this.loopError !== null) {
      const err: string = this.loopError;
      this.loopError = null;
      throw new Error(err);
    }
    if (this.pcmTotal === 0) {
      throw new Error('录音数据为空(时长过短)');
    }
    // 组装 WAV:头 + PCM
    const wav: Uint8Array = new Uint8Array(44 + this.pcmTotal);
    wav.set(buildWavHeader(this.pcmTotal), 0);
    let offset: number = 44;
    for (const chunk of this.pcmChunks) {
      wav.set(chunk, offset);
      offset += chunk.length;
    }
    this.pcmChunks = [];
    return wav;
  }

  /** 立即丢弃本次会话(不产出 WAV,静默回收设备) */
  async abort(): Promise<void> {
    this.running = false;
    this.pcmChunks = [];
    this.pcmTotal = 0;
    const capturer: audio.AudioCapturer | null = this.capturer;
    this.capturer = null;
    if (capturer !== null) {
      try {
        await capturer.stop();
      } catch (ignore) {
      }
      try {
        await capturer.release();
      } catch (ignore) {
      }
    }
  }
}
