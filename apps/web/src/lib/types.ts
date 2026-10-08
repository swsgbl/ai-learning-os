// seed 命名（mcq/tf/short）与 QuestionSpec 命名（mcq/true_false/short_answer/...）并存，
// 后端透传原始题型名；前端按「有无 options」决定渲染形态
export type QuestionType =
  | "mcq"
  | "tf"
  | "short"
  | "multiple_select"
  | "true_false"
  | "short_answer"
  | "numeric"
  | "math"
  | "coding"
  | "essay";
export type Difficulty = "intro" | "core" | "advanced";
export type ExamMode = "voice" | "exam";
export type ExamStatus = "active" | "submitted" | "expired";

export type PaperSummary = {
  id: string;
  title: string;
  subtitle: string;
  source: string;
  university?: string;
  year?: number;
  subject: string;
  difficulty: Difficulty;
  duration_minutes: number;
  tags: string[];
  origin_url?: string;
};

export type QuestionOption = {
  key: string;
  text: string;
};

export type PublicQuestion = {
  id: string;
  type: QuestionType;
  stem: string;
  options?: QuestionOption[];
};

export type ReviewQuestion = PublicQuestion & {
  answer: string;
  explanation: string;
  angles: {
    concept: string;
    method: string;
    mistake: string;
    variant: string;
  };
  knowledge: string[];
};

export type ExamSession = {
  exam_id: string;
  paper_id: string;
  paper_title: string;
  mode: ExamMode;
  status: ExamStatus;
  server_started_at: string;
  server_end_at: string;
  server_remaining_seconds: number;
  questions: PublicQuestion[];
  answers: Record<string, string>;
  next_sequence: number;
};

export type RubricCriterion = {
  point: string;
  achieved: boolean | null;
  evidence_id?: string | null;
};

// M2-10 essay 结构化判分明细（prompt pack E schema）
export type RubricDetail = {
  rule_version: string;
  criteria: RubricCriterion[];
  score_ratio?: number | null;
  confidence: number;
  judge_model: string;
  prompt_hash?: string;
};

export type GradedItem = {
  question_id: string;
  given: string;
  correct: boolean | null;
  expected: string;
  explanation: string;
  angles: ReviewQuestion["angles"];
  rubric?: RubricDetail | null;
};

export type Submission = {
  exam_id: string;
  paper_id: string;
  paper_title: string;
  mode: ExamMode;
  status: ExamStatus;
  score: number;
  correct_count: number;
  total_count: number;
  duration_seconds: number;
  items: GradedItem[];
  questions: ReviewQuestion[];
};

// M2-11 考试报告：题分/概念分/错题/补救任务/证据链接
export type ReportItem = {
  question_id: string;
  sequence: number;
  question_type: string;
  stem: string;
  given: string;
  expected: string;
  correct: boolean | null;
  score: number | null;
  max_score: number;
  explanation: string;
  knowledge: string[];
  evidence_ids: string[];
  score_ratio?: number | null;
};

export type ConceptScore = {
  concept: string;
  correct: number;
  total: number;
  reviewed: number;
  ratio: number | null;
};

export type RemediationTask = {
  kind: "review_concept" | "variant_practice";
  title: string;
  detail: string;
  question_id: string;
};

export type MistakeEntry = {
  question_id: string;
  stem: string;
  given: string;
  expected: string;
  explanation: string;
  diagnosis: string;
  knowledge: string[];
  evidence_ids: string[];
  remediation_task_ids: number[];
};

export type ExamReport = {
  exam_id: string;
  paper_title: string;
  mode: ExamMode;
  score: number;
  score_earned: number;
  score_max: number;
  correct_count: number;
  total_count: number;
  reviewed_count: number;
  items: ReportItem[];
  concepts: ConceptScore[];
  mistakes: MistakeEntry[];
  remediation_tasks: RemediationTask[];
  evidence_ids: string[];
};

// --- M10-02 治理与学习工作台 ---

export type UserRole = "learner" | "admin";

// 用户资料（GET /api/v1/auth/me）：role 只驱动入口渲染，安全边界在服务端 require_admin
export type UserProfile = {
  id: string;
  username: string;
  role: UserRole;
  created_at: string;
};

// 草稿状态机：pending_review -> approved | rejected（终态不可逆，重复审核 409）
export type DraftStatus = "pending_review" | "approved" | "rejected";

// 审计日志（GET /api/v1/audit，admin-only；不含任何密钥类字段）
export type AuditEntry = {
  id: number;
  actor_id: string | null;
  actor_username: string | null;
  action: string;
  target_type: string;
  target_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  request_id: string;
  created_at: string;
};

// 今日计划（GET /api/v1/student/daily-plan）
export type PlanTaskKind = "new_learning" | "review" | "mistake_retry";

export type PlanTask = {
  kind: PlanTaskKind | string; // 后端新增 kind 时如实透出，前端按已知映射展示
  question_id: string;
  concept_ids: string[];
  title: string;
  reason: string;
  priority: number;
};

export type DailyPlan = {
  generated_at: string;
  plan_date: string;
  task_count: number;
  review_count: number;
  mistake_retry_count: number;
  new_learning_count: number;
  tasks: PlanTask[];
};

// 学生状态（GET /api/v1/student/states）
export type ConceptState = {
  concept_id: string;
  mastery: number;
  confidence: number;
  forgetting_risk: number;
  evidence_count: number;
  correct_count: number;
  wrong_count: number;
  first_event_at: string;
  last_event_at: string;
  updated_at: string;
};

export type StudentStates = {
  concept_count: number;
  states: ConceptState[];
  weak_concepts: string[];
};

// 课程导入草稿（GET /api/v1/courses/import-drafts）
export type ImportDraft = {
  id: string;
  title: string;
  status: DraftStatus;
  source_resource_id: string;
  source_license_state: string;
  reuse_admission: string;
  concepts: string[];
  resource_refs: string[];
  extraction_note: string;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
};

// 课程生成草稿（GET /api/v1/courses/generation-drafts）；plan 为服务端生成的宽松 dict
export type GenerationPlanChapter = {
  chapter_no: number;
  concept_id: string;
  title: string;
  difficulty: string;
};

export type GenerationPlan = {
  goal?: string;
  matched_competencies?: string[];
  ordered_concepts?: string[];
  dag_version?: number;
  outline?: GenerationPlanChapter[];
  lessons?: Array<{ chapter_no: number; objectives: string[]; resources: string[] }>;
  assessments?: Array<{
    chapter_no: number;
    concept_id: string;
    assessment_kind: string;
    stem: string;
  }>;
  remediation?: Array<{ chapter_no: number; concept_id: string; review_chapters: number[] }>;
  generation_note?: string;
};

export type GenerationDraft = {
  id: string;
  goal: string;
  status: DraftStatus;
  dag_version: number;
  chapter_count: number;
  generation_note: string;
  plan: GenerationPlan;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
};

// 试卷抽取草稿（GET /api/v1/papers/import-drafts）
export type DraftQuestion = {
  question_no: number;
  stem: string;
  question_type: string;
  score: number | null;
  page_start: number | null;
  page_end: number | null;
  options: Record<string, unknown>[];
};

export type PaperDraft = {
  id: string;
  resource_id: string;
  status: DraftStatus;
  questions: DraftQuestion[];
  question_count: number;
  extraction_note: string;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
};

// 变式题草稿（GET /api/v1/questions/variant-drafts）
export type VariantEvidence = {
  source_question_no: number;
  page_start: number | null;
  page_end: number | null;
  resource_id: string;
  source_stem_excerpt: string;
};

export type VariantItem = {
  variant_no: number;
  transform: string;
  transform_detail: string;
  stem: string;
  question_type: string;
  score: number | null;
  options: Record<string, unknown>[];
  concept_ids: string[];
  evidence: VariantEvidence;
  solvable_note: string;
};

export type VariantDraft = {
  id: string;
  status: DraftStatus;
  variants: VariantItem[];
  variant_count: number;
  generation_note: string;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
};

// --- M14-35 语音 ---

// 房间 token（POST /api/v1/voice/token）：token 字段只经局部变量喂给 SDK，
// 绝不进入 React state / DOM / 日志；ws_url 是浏览器可达地址（非敏感，可展示）
export type VoiceTokenResponse = {
  token: string;
  room: string;
  identity: string;
  role: string;
  expires_at: string;
  ws_url: string;
};

// --- M14-250 服务端 TTS 通道（services/api voice.py providers 视图） ---
// snake_case 与服务端 ProvidersOut/ProviderView 一一对应：provider 是
// 当前 VOICE_MODE 路由的实际选择，fallback=true 表示想用的 provider
// 未配置已降级零依赖替身（tone，非真实语音）——原样透出不虚报。
export type VoiceProviderView = {
  requested: string | null;
  provider: string;
  fallback: boolean;
};

export type VoiceProvidersView = {
  voice_mode: string;
  asr: VoiceProviderView;
  tts: VoiceProviderView;
  privacy_store_audio: boolean;
  privacy_send_context_to_cloud: boolean;
};

// --- M14-248 服务端权威 VoiceSession 契约（services/api voice.py） ---
// snake_case 与服务端 Pydantic 契约一一对应（VoiceSessionOut 族），字段
// 不增不减。status 保留服务端原词（8 状态 FSM：SESSION_READY /
// READING_QUESTION / READING_OPTIONS / WAITING_ANSWER / CLARIFYING /
// ANSWER_COMMITTED / NEXT_QUESTION / REPORT_READY），客户端不定义状态
// 集合、不推演转移——未知值按 string 原样接收，不猜语义；FSM 校验与
// 状态/答案/判分一律以服务端为唯一真相源。

// 会话（POST /sessions、GET /sessions?exam_id=、GET /sessions/{id}）
export type VoiceSession = {
  session_id: string;
  exam_id: string;
  status: string;
  question_index: number;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type VoiceSessions = {
  items: VoiceSession[];
};

export type VoiceSessionCreateRequest = {
  exam_id: string;
};

// 恢复（GET /sessions/{id}/resume）：question 为当前题公开字段投影
// （id/type/stem/options——不含 answer/explanation），终态会话 409 无此视图
export type VoiceResumeQuestion = {
  id: string;
  type: string;
  stem: string;
  options: QuestionOption[];
};

export type VoiceSessionResume = {
  session: VoiceSession;
  question: VoiceResumeQuestion | null;
  committed_answer: string | null;
  question_total: number;
};

// 命令（POST /sessions/{id}/commands）：type 为服务端 15 事件原词
// （start_reading/read_options/repeat/answer_proposed/…），事件合法性由
// 服务端 FSM 校验（未知命令 422、非法迁移 409）。可空字段与服务端
// `str | None = None` 一一对应（显式传 null = 缺省语义）
export type VoiceSessionCommandRequest = {
  type: string;
  question_id?: string | null;
  answer?: string | null;
  ambiguous?: boolean;
  expected_revision?: number | null;
};

export type VoiceSessionCommandResult = {
  applied_event: string;
  from_status: string;
  session: VoiceSession;
  clarified_question?: string | null;
  question_total?: number | null;
};

// 意图（POST /sessions/{id}/intents）：transcript 由服务端解析并直接应用
// 到 FSM；unknown/pause/resume 不应用（fsm_applied=false）
export type VoiceSessionIntentRequest = {
  transcript: string;
  expected_revision?: number | null;
};

export type VoiceSessionIntentResult = {
  transcript: string;
  intent: string;
  letter?: string | null;
  ordinal?: number | null;
  ambiguous: boolean;
  fsm_command?: string | null;
  fsm_applied: boolean;
  applied_event?: string | null;
  session?: VoiceSession | null;
  clarified_question?: string | null;
};

// 规范化答案（POST /sessions/{id}/answers）：event_id 为客户端幂等键
// （一次逻辑事件生成一次、重试复用同一值——服务端凭它去重，见
// newVoiceAnswerEventId）；服务端从 transcript 重新规范化，不信任
// normalized_answer/confidence 参考值。可空字段与服务端
// `str | None = None` 一一对应（显式传 null = 缺省语义）
export type VoiceSessionAnswerRequest = {
  transcript: string;
  event_id: string;
  question_id?: string | null;
  normalized_answer?: string | null;
  confidence?: number;
};

export type VoiceSessionAnswerResult = {
  event_id: string;
  idempotent: boolean;
  accepted: boolean;
  normalized_answer?: string | null;
  intent: string;
  question_id: string;
  session?: VoiceSession | null;
  clarified_question?: string | null;
};

// 语音报告（GET /sessions/{id}/report）：REPORT_READY + 判分完成后的播报
// 投影——只投影不判定，分数/错题/补救全部来自服务端判分结果
export type VoiceMistakeSummary = {
  question_id: string;
  stem_preview: string;
  your_answer: string;
  correct_answer: string;
  concepts: string[];
  diagnosis: string;
};

export type VoiceRemediationSummary = {
  concept: string;
  actions: string[];
  detail: string;
  question_ids: string[];
};

export type VoiceSessionReport = {
  session_id: string;
  exam_id: string;
  spoken_text: string;
  mistake_summary: VoiceMistakeSummary[];
  remediation_summary: VoiceRemediationSummary[];
  written_report_url: string;
};
