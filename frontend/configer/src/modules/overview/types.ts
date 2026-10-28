// 概览模块的数据形状（与旧 modules/overview.js 读取的字段一致）。

export interface OverviewPlugin {
  id?: string;
  enabled?: boolean;
  [key: string]: unknown;
}

export interface OverviewService {
  key?: string;
  name?: string;
  is_running?: boolean;
  [key: string]: unknown;
}

export interface RecentError {
  timestamp?: string;
  name?: string;
  message?: string;
  [key: string]: unknown;
}

export interface RuntimeSummary {
  current_agent?: string;
  [key: string]: unknown;
}

/**
 * 「版本更新」卡片的结果区状态。旧实现直接拼 innerHTML，这里改成结构化状态，
 * 文案与颜色（danger / accent / muted）保持一一对应。
 */
export type UpdateResult =
  | { kind: 'none' }
  | { kind: 'error'; text: string }
  | { kind: 'latest' }
  | {
      kind: 'available';
      latestTag: string;
      latestVersion: string;
      releaseBody: string;
      assetName: string;
    }
  | { kind: 'manual'; tag: string; version: string; assetName: string }
  | { kind: 'prepared' };

/** check / manual 接口返回体（字段按旧实现读取，其余保留原样）。 */
export interface UpdateCheckResponse {
  status?: string;
  error?: string;
  has_update?: boolean;
  current_tag?: string;
  latest_tag?: string;
  latest_version?: string;
  release_body?: string;
  asset_name?: string;
  [key: string]: unknown;
}

export interface UpdateManualResponse {
  status?: string;
  error?: string;
  tag?: string;
  version?: string;
  asset_name?: string;
  [key: string]: unknown;
}

export interface DownloadStatusResponse {
  status?: string;
  error?: string;
  download_id?: string;
  [key: string]: unknown;
}

export interface DownloadProgress {
  visible: boolean;
  percent: number;
  text: string;
}
