/**
 * TokenVault.ets — bearer token 安全持久化(M14-89)
 *
 * 职责边界(刻意收窄):
 *   - 唯一职责:把 bearer token 存入/读出 HarmonyOS Asset Store Kit
 *     (@ohos.security.asset,系统级加密存储,HUKS 背书);
 *   - 绝不把 token 写进 preferences、普通文件、日志、布局或任何明文载体;
 *   - 绝不存储密码(密码只经 login 请求体一次性使用,任何路径不落盘);
 *   - 附加属性刻意最小:ALIAS + SECRET,不设 IS_PERSISTENT(免系统权限),
 *     不设锁屏/生物认证(本切片不加二次认证面);
 *   - DUPLICATED(24000003)语义 = 覆盖写:先 remove(NOT_FOUND 忽略)再 add;
 *   - 所有失败如实返回 fail-closed 结果;读不到就是读不到,不虚构会话。
 *
 * Asset Store Kit 契约(本地 SDK @ohos.security.asset.d.ts 核对):
 *   add(attributes) -> Promise<void>;query(query) -> Promise<Array<AssetMap>>;
 *   remove(query) -> Promise<void>;
 *   Tag.SECRET / Tag.ALIAS = TagType.BYTES;ErrorCode.NOT_FOUND = 24000002,
 *   DUPLICATED = 24000003(模拟器 API 24 实测可用,见 evidence README)。
 */

import { asset } from '@kit.AssetStoreKit';
import { util } from '@kit.ArkTS';
import { BusinessError } from '@kit.BasicServicesKit';

/** Asset 别名(本应用唯一的 token 条目;bytes 型 tag) */
const TOKEN_ALIAS: string = 'aios.bearer.token.v1';

/** 写/删操作结果 */
export interface VaultResult {
  ok: boolean;
  error: string;
}

/** 读取结果(token 仅在 ok 时非空) */
export interface VaultLoadResult {
  ok: boolean;
  token: string | null;
  error: string;
}

function vaultOk(): VaultResult {
  return { ok: true, error: 'ok' };
}

function vaultFail(error: string): VaultResult {
  return { ok: false, error: error };
}

function loadFail(error: string): VaultLoadResult {
  return { ok: false, token: null, error: error };
}

function asBusinessError(e: Object | undefined): BusinessError<void> | null {
  if (e === null || e === undefined) {
    return null;
  }
  try {
    const rec: Record<string, Object> = e as Record<string, Object>;
    const raw: Object | undefined = rec['code'];
    if (typeof raw === 'number') {
      const msg: Object | undefined = rec['message'];
      const be: BusinessError<void> = {
        code: raw,
        message: typeof msg === 'string' ? msg : '',
        name: 'BusinessError'
      };
      return be;
    }
  } catch (err) {
    return null;
  }
  return null;
}

function utf8(text: string): Uint8Array {
  const enc: util.TextEncoder = util.TextEncoder.create();
  return enc.encodeInto(text);
}

function utf8Decode(bytes: Uint8Array): string {
  const dec: util.TextDecoder = util.TextDecoder.create('utf-8');
  return dec.decodeToString(bytes);
}

/**
 * 写入 token(覆盖语义)。任何失败如实返回 fail-closed。
 * 空白/空 token 视为非法输入直接拒绝(不会把空白 token 存进 vault)。
 */
export async function saveTokenToVault(token: string): Promise<VaultResult> {
  if (!token || token.trim().length === 0 || token !== token.trim()) {
    return vaultFail('token 非法(空或含首尾空白),拒绝写入安全存储');
  }
  try {
    const attrs: asset.AssetMap = new Map();
    attrs.set(asset.Tag.SECRET, utf8(token));
    attrs.set(asset.Tag.ALIAS, utf8(TOKEN_ALIAS));
    await asset.add(attrs);
    return vaultOk();
  } catch (e) {
    const be = asBusinessError(e);
    if (be !== null && be.code === 24000003) {
      // DUPLICATED: 先删后写(覆盖)
      try {
        const q: asset.AssetMap = new Map();
        q.set(asset.Tag.ALIAS, utf8(TOKEN_ALIAS));
        await asset.remove(q);
      } catch (rmErr) {
        const rmBe = asBusinessError(rmErr);
        if (rmBe === null || rmBe.code !== 24000002) {
          const code: number = rmBe !== null ? rmBe.code : -1;
          return vaultFail(`覆盖写失败(清理旧 token): code=${code}`);
        }
      }
      try {
        const attrs2: asset.AssetMap = new Map();
        attrs2.set(asset.Tag.SECRET, utf8(token));
        attrs2.set(asset.Tag.ALIAS, utf8(TOKEN_ALIAS));
        await asset.add(attrs2);
        return vaultOk();
      } catch (e2) {
        const be2 = asBusinessError(e2);
        const code2: number = be2 !== null ? be2.code : -1;
        return vaultFail(`写入安全存储失败: code=${code2}`);
      }
    }
    const code: number = be !== null ? be.code : -1;
    return vaultFail(`写入安全存储失败: code=${code}`);
  }
}

/**
 * 读取 token;无条目/任何失败返回 {ok:false, token:null}。
 * 读出的 token 只进内存,绝不写日志/布局/普通文件。
 */
export async function loadTokenFromVault(): Promise<VaultLoadResult> {
  try {
    const q: asset.AssetMap = new Map();
    q.set(asset.Tag.ALIAS, utf8(TOKEN_ALIAS));
    q.set(asset.Tag.RETURN_TYPE, asset.ReturnType.ALL);
    const rows: Array<asset.AssetMap> = await asset.query(q);
    if (rows.length === 0) {
      return loadFail('no_token');
    }
    const secret: asset.Value | undefined = rows[0].get(asset.Tag.SECRET);
    if (secret === undefined || typeof secret !== 'object') {
      return loadFail('token_secret_missing');
    }
    const token: string = utf8Decode(secret as Uint8Array);
    if (token.length === 0 || token !== token.trim()) {
      return loadFail('token_invalid_shape');
    }
    const out: VaultLoadResult = { ok: true, token: token, error: 'ok' };
    return out;
  } catch (e) {
    const be = asBusinessError(e);
    if (be !== null && be.code === 24000002) {
      return loadFail('no_token');
    }
    const code: number = be !== null ? be.code : -1;
    return loadFail(`读取安全存储失败: code=${code}`);
  }
}

/**
 * 删除 token(登出/清理)。NOT_FOUND 视为成功(幂等);其余失败如实返回。
 */
export async function clearTokenVault(): Promise<VaultResult> {
  try {
    const q: asset.AssetMap = new Map();
    q.set(asset.Tag.ALIAS, utf8(TOKEN_ALIAS));
    await asset.remove(q);
    return vaultOk();
  } catch (e) {
    const be = asBusinessError(e);
    if (be !== null && be.code === 24000002) {
      return vaultOk();
    }
    const code: number = be !== null ? be.code : -1;
    return vaultFail(`清空安全存储失败: code=${code}`);
  }
}
