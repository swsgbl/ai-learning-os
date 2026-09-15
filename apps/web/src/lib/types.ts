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
