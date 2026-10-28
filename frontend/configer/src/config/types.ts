export type ConfigScope = 'public' | 'private';

export type ConfigDict = Record<string, unknown>;

export interface ConfigBundle {
  public: ConfigDict;
  private: ConfigDict;
}

export type BannerType = 'info' | 'success' | 'error';

export interface ModuleDef {
  id: string;
  title: string;
  desc: string;
  nav?: string;
}

export interface AiGroupRow {
  key: string;
  scope?: ConfigScope;
  advanced?: boolean;
  gated?: string;
  readonly?: boolean;
}

export interface AiGroupSpec {
  id: string;
  title: string;
  desc?: string;
  keys: AiGroupRow[];
  footer?: string;
  folded?: boolean;
  custom?: 'providers';
}

export interface FieldMeta {
  label: string;
  help: string;
}

/** 数字字段的控件规格（与旧 field-render.js 的 getNumberFieldSpec 对应）。 */
export interface NumberFieldSpec {
  type: 'range' | 'number';
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}

/** InfoCard 的一行。 */
export interface InfoRow {
  label: string;
  value: unknown;
}

/** 表格搜索/分页状态（按卡片标题持久化）。 */
export interface TableUIState {
  page: number;
  query: string;
}

export interface ProviderConfig {
  name: string;
  base_url?: string;
  key?: string;
  api_format?: string;
  thinking_type?: string;
  models?: string[];
  model_context_lengths?: Record<string, number>;
  model_max_output_tokens?: Record<string, number>;
  model_api_formats?: Record<string, string>;
  model_server_tools?: Record<string, string[]>;
  catalog_provider_id?: string;
  catalog_model_matches?: Record<string, unknown>;
  disabled?: boolean;
  [key: string]: unknown;
}

export interface ModelEntry {
  provider: string;
  model: string;
  spec: string;
}

export interface ProviderBundle {
  providers: ProviderConfig[];
  main_model: string;
  subagent_models: string[];
  resolved_formats: Record<string, Record<string, { format: string; source: string; reason?: string }>>;
  catalog: Record<string, unknown>;
}

export interface FormatDecision {
  format: string;
  source: string;
  reason?: string;
}
