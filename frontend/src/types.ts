export type ModelRecord = {
  id: string;
  name: string;
  type: string;
  filename: string;
  path: string;
  uploaded_at?: string;
  started_at?: string | null;
  status?: string;
  source?: string;
  runtime?: Record<string, unknown>;
  copy_to_storage?: boolean;
  file_exists?: boolean;
  file_size?: number;
  validation_error?: string;
  last_log_path?: string;
};

export type ThemeSettings = {
  accent: string;
  hero_text: string;
  chrome_start: string;
  chrome_end: string;
  chrome_text: string;
  background_start: string;
  background_end: string;
  panel: string;
  panel_alt: string;
  border: string;
  text: string;
  muted: string;
  user_bubble: string;
  assistant_bubble: string;
  success: string;
  warning: string;
  danger: string;
};

export type BrandingSettings = {
  title: string;
  subtitle: string;
  logo_url: string;
  logo_width: number;
  logo_height: number;
  logo_radius: number;
  logo_padding: number;
  logo_fit: 'contain' | 'cover' | 'fill';
};

export type RuntimeSettings = {
  n_ctx: number;
  n_batch: number;
  n_threads: number;
  n_threads_batch: number;
  n_gpu_layers: number;
  main_gpu: number;
  split_mode: 'none' | 'layer' | 'row';
  tensor_split: string;
  temperature: number;
  max_tokens: number;
  top_k: number;
  top_p: number;
  min_p: number;
  repeat_penalty: number;
  seed: number;
  offload_kqv: boolean;
  flash_attn: boolean;
  op_offload: boolean;
  swa_full: boolean;
  use_mmap: boolean;
  use_mlock: boolean;
  verbose_runtime: boolean;
  gpu_fallback_to_cpu: boolean;
  warm_policy: 'keep_hot' | 'manual' | 'unload_after_idle';
  idle_unload_sec: number;
  preload_on_start: boolean;
};

export type HubSettings = {
  enabled: boolean;
  base_url: string;
  models_endpoint: string;
  pull_endpoint: string;
  token: string;
  timeout_sec: number;
};

export type ServerSettings = {
  host: string;
  port: number;
  public_base_url: string;
  openai_compat_enabled: boolean;
  openai_compat_path: string;
  cors_origins: string[];
  api_key: string;
};

export type LayoutSettings = {
  app_max_width: number;
  card_radius: number;
  hero_compact: boolean;
  left_panel_width: number;
  center_panel_min_width: number;
  right_panel_width: number;
};

export type EngineSettings = {
  branding: BrandingSettings;
  theme: ThemeSettings;
  layout: LayoutSettings;
  runtime: RuntimeSettings;
  server: ServerSettings;
  hub: HubSettings;
};

export type BootstrapPayload = {
  models: ModelRecord[];
  settings: EngineSettings;
};

export type StatusTone = 'idle' | 'busy' | 'success' | 'warning' | 'error';

export type StatusState = {
  tone: StatusTone;
  title: string;
  detail?: string;
};

export type RemoteHubModel = {
  id?: string;
  name?: string;
  type?: string;
  filename?: string;
  size?: string | number;
  description?: string;
  [key: string]: unknown;
};

export type ChatMessage = {
  id?: string;
  role: 'user' | 'assistant';
  text: string;
  answer?: string;
  reasoning?: string;
  pending?: boolean;
  phase?: 'thinking' | 'reasoning_done' | 'typing' | 'done' | 'error';
  answer_state?: string;
  reasoning_truncated?: boolean;
  finish_reason?: string | null;
  elapsed_ms?: number;
  request_id?: string;
  log_path?: string;
  log_excerpt?: string[];
  runtime_mode?: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    [key: string]: unknown;
  };
};

export type RuntimeStatus = {
  model_id: string;
  model_name: string;
  state: string;
  loaded_at?: string;
  last_used_at?: string;
  idle_seconds: number;
  policy: string;
  runtime_mode?: string;
  fallback_reason?: string;
  requested_n_gpu_layers?: number;
  effective_n_gpu_layers?: number;
  gpu_runtime_ready?: boolean;
  worker_python?: string;
  package_path?: string;
  gpu_backend_flags?: string[];
  gpu_memory_before_load?: Record<string, unknown>;
  gpu_memory_after_load?: Record<string, unknown>;
};

export type RuntimeDiagnostics = {
  python: string;
  platform: string;
  package_installed: boolean;
  package_version: string;
  package_path: string;
  worker_python?: string;
  worker_python_source?: string;
  source_root?: string;
  runtime_build?: Record<string, unknown>;
  nvidia_smi_found: boolean;
  nvidia_smi: string;
  supported_parameters: string[];
  gpu_related_supported: string[];
  supports_gpu_offload?: boolean | null;
  system_info: string;
  gpu_backend_flags: string[];
  gpu_runtime_ready?: boolean;
  missing_cuda_dlls?: string[];
  likely_cpu_build: boolean;
  summary: string;
  recommendations: string[];
  install_commands: {
    cuda: string;
    vulkan: string;
    cpu: string;
  };
};
