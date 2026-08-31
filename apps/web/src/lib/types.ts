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
