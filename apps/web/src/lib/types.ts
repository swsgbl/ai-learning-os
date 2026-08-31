export type QuestionType = "mcq" | "tf" | "short";
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

export type GradedItem = {
  question_id: string;
  given: string;
  correct: boolean;
  expected: string;
  explanation: string;
  angles: ReviewQuestion["angles"];
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
