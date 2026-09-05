"use client";

// M10-02 治理工作台：四类内容草稿审核 + 审计回查。
// 入口渲染依据 /api/v1/auth/me 的 role（admin / 本地模式可见），learner 直接给
// 简洁无泄露提示；数据边界仍由后端 require_admin 把守——learner 强行访问队列
// 时各视图收到 403 也只显示同样的提示，不泄露队列内容。
import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ClipboardList, Cog, FileText, ScrollText, Shuffle } from "lucide-react";
import { API_BASE, api } from "@/lib/api";
import { probeAuth, type AuthState } from "@/lib/auth";
import type { GenerationDraft, ImportDraft, PaperDraft, VariantDraft } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { AuditLog } from "@/components/governance/audit-log";
import { GovTabs } from "@/components/governance/gov-tabs";
import { DraftQueue, formatTime, type DraftKindConfig } from "@/components/governance/draft-queue";

// --- 四类草稿的展示投影（字段各异，统一降级为 元数据行 + 详情正文） ---

function NoteBlock({ title, text }: { title: string; text: string | null | undefined }) {
  if (!text) return null;
  return (
    <p className="mt-2 text-xs leading-relaxed text-muted">
      {title}：<span className="text-ink/80">{text}</span>
    </p>
  );
}

function MetaChips({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="text-ink/60">（无）</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <Badge key={item} className="font-normal">
          {item}
        </Badge>
      ))}
    </span>
  );
}

const importQueue: DraftKindConfig<ImportDraft> = {
  key: "courseImport",
  label: "课程导入",
  load: () => api.drafts.courseImport.list(),
  refreshOne: (id) => api.drafts.courseImport.get(id),
  approve: (id, note) => api.drafts.courseImport.approve(id, note),
  reject: (id, note) => api.drafts.courseImport.reject(id, note),
  idOf: (draft) => draft.id,
  statusOf: (draft) => draft.status,
  titleOf: (draft) => draft.title,
  metaOf: (draft) => [
    { label: "概念", value: `${draft.concepts.length} 个` },
    { label: "授权", value: draft.source_license_state },
    { label: "准入", value: draft.reuse_admission },
    { label: "创建", value: formatTime(draft.created_at) },
  ],
  detailOf: (draft) => (
    <div>
      <p className="text-xs text-muted">
        来源资源 <span className="font-mono">{draft.source_resource_id}</span> · 引用资源{" "}
        {draft.resource_refs.length} 个
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">概念覆盖</span>
        <MetaChips items={draft.concepts} />
      </div>
      <NoteBlock title="抽取说明" text={draft.extraction_note} />
      <NoteBlock title="审核备注" text={draft.review_note} />
      {draft.reviewed_at && (
        <p className="mt-1 text-xs text-subtle">审核时间 {formatTime(draft.reviewed_at)}</p>
      )}
    </div>
  ),
};

const generationQueue: DraftKindConfig<GenerationDraft> = {
  key: "courseGeneration",
  label: "课程生成",
  load: () => api.drafts.courseGeneration.list(),
  refreshOne: (id) => api.drafts.courseGeneration.get(id),
  approve: (id, note) => api.drafts.courseGeneration.approve(id, note),
  reject: (id, note) => api.drafts.courseGeneration.reject(id, note),
  idOf: (draft) => draft.id,
  statusOf: (draft) => draft.status,
  titleOf: (draft) => draft.goal,
  metaOf: (draft) => [
    { label: "章节", value: `${draft.chapter_count} 章` },
    { label: "DAG", value: `v${draft.dag_version}` },
    { label: "创建", value: formatTime(draft.created_at) },
  ],
  detailOf: (draft) => (
    <div>
      <NoteBlock title="生成说明" text={draft.generation_note} />
      {draft.plan.outline && draft.plan.outline.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <p className="text-xs font-medium text-muted">大纲</p>
          {draft.plan.outline.map((chapter) => (
            <p key={chapter.chapter_no} className="text-sm">
              <span className="mr-2 text-xs text-subtle tabular-nums">第{chapter.chapter_no}章</span>
              {chapter.title}
              <span className="ml-2 text-xs text-muted">({chapter.concept_id})</span>
            </p>
          ))}
        </div>
      )}
      <NoteBlock title="审核备注" text={draft.review_note} />
      {draft.reviewed_at && (
        <p className="mt-1 text-xs text-subtle">审核时间 {formatTime(draft.reviewed_at)}</p>
      )}
    </div>
  ),
};

const paperQueue: DraftKindConfig<PaperDraft> = {
  key: "paperExtraction",
  label: "试卷抽取",
  load: () => api.drafts.paperExtraction.list(),
  refreshOne: (id) => api.drafts.paperExtraction.get(id),
  approve: (id, note) => api.drafts.paperExtraction.approve(id, note),
  reject: (id, note) => api.drafts.paperExtraction.reject(id, note),
  idOf: (draft) => draft.id,
  statusOf: (draft) => draft.status,
  titleOf: (draft) => `${draft.question_count} 题试卷草稿`,
  metaOf: (draft) => [
    { label: "题量", value: `${draft.question_count} 题` },
    { label: "资源", value: draft.resource_id },
    { label: "创建", value: formatTime(draft.created_at) },
  ],
  detailOf: (draft) => (
    <div>
      <NoteBlock title="抽取说明" text={draft.extraction_note} />
      <div className="mt-3 space-y-2">
        <p className="text-xs font-medium text-muted">题目</p>
        {draft.questions.map((question) => (
          <div key={question.question_no} className="rounded-lg bg-bg p-3">
            <p className="text-xs text-muted">
              第{question.question_no}题 · {question.question_type}
              {question.score !== null && ` · ${question.score} 分`}
            </p>
            <p className="mt-1 text-sm leading-relaxed">{question.stem}</p>
          </div>
        ))}
      </div>
      <NoteBlock title="审核备注" text={draft.review_note} />
      {draft.reviewed_at && (
        <p className="mt-1 text-xs text-subtle">审核时间 {formatTime(draft.reviewed_at)}</p>
      )}
    </div>
  ),
};

const variantQueue: DraftKindConfig<VariantDraft> = {
  key: "variantQuestion",
  label: "变式题",
  load: () => api.drafts.variantQuestion.list(),
  refreshOne: (id) => api.drafts.variantQuestion.get(id),
  approve: (id, note) => api.drafts.variantQuestion.approve(id, note),
  reject: (id, note) => api.drafts.variantQuestion.reject(id, note),
  idOf: (draft) => draft.id,
  statusOf: (draft) => draft.status,
  titleOf: (draft) => `${draft.variant_count} 道变式题草稿`,
  metaOf: (draft) => [
    { label: "变式数", value: `${draft.variant_count} 道` },
    { label: "创建", value: formatTime(draft.created_at) },
  ],
  detailOf: (draft) => (
    <div>
      <NoteBlock title="生成说明" text={draft.generation_note} />
      <div className="mt-3 space-y-2">
        <p className="text-xs font-medium text-muted">变式题</p>
        {draft.variants.map((variant) => (
          <div key={variant.variant_no} className="rounded-lg bg-bg p-3">
            <p className="text-xs text-muted">
              变式{variant.variant_no} · {variant.transform}
              {variant.score !== null && ` · ${variant.score} 分`}
            </p>
            <p className="mt-1 text-sm leading-relaxed">{variant.stem}</p>
            <p className="mt-1 text-xs text-subtle">
              证据：第{variant.evidence.source_question_no}题
              {variant.evidence.source_stem_excerpt
                ? `「${variant.evidence.source_stem_excerpt.slice(0, 40)}」`
                : ""}
            </p>
          </div>
        ))}
      </div>
      <NoteBlock title="审核备注" text={draft.review_note} />
      {draft.reviewed_at && (
        <p className="mt-1 text-xs text-subtle">审核时间 {formatTime(draft.reviewed_at)}</p>
      )}
    </div>
  ),
};

const GOV_TABS = [
  { id: "import", label: "课程导入", icon: ClipboardList },
  { id: "generation", label: "课程生成", icon: Cog },
  { id: "paper", label: "试卷抽取", icon: FileText },
  { id: "variant", label: "变式题", icon: Shuffle },
  { id: "audit", label: "审计日志", icon: ScrollText },
] as const;

// --- 页面 ---

function NoAccess({ hint }: { hint?: ReactNode }) {
  return (
    <Card className="p-6">
      <h2 className="font-display text-xl">需要管理员权限</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
        治理工作台仅对管理员开放。如果你是管理员，请确认当前登录账号已被提升；普通学习账号无法审核内容草稿或查看审计日志。
      </p>
      {hint}
    </Card>
  );
}

export default function GovernancePage() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [tab, setTab] = useState<string>("import"); // 四类草稿 + 审计

  useEffect(() => {
    let active = true;
    probeAuth(API_BASE)
      .then((state) => {
        if (active) setAuth(state);
      })
      .catch(() => {
        if (active) setAuth({ mode: "anonymous" });
      });
    return () => {
      active = false;
    };
  }, []);

  if (!auth) {
    return (
      <div className="space-y-6">
        <PageHeader />
        <Card className="p-5 text-sm text-muted">正在确认访问权限……</Card>
      </div>
    );
  }

  // 本地模式（未配置 AUTH_SECRET）：单用户即治理者，后端放行——如实透出；
  // role 未披露（旧后端）时同样放行渲染，由各视图的 403 兜底，安全边界始终在服务端
  const showPanel =
    auth.mode === "disabled" || (auth.mode === "authenticated" && auth.role !== "learner");

  return (
    <div className="space-y-6">
      <PageHeader />
      {auth.mode === "anonymous" && (
        <Card className="p-6">
          <h2 className="font-display text-xl">请先登录</h2>
          <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
            治理工作台需要登录后访问。
          </p>
          <Button asChild className="mt-4">
            <Link href="/login">前往登录</Link>
          </Button>
        </Card>
      )}
      {auth.mode === "authenticated" && auth.role === "learner" && <NoAccess />}
      {auth.mode === "disabled" && (
        <Card className="p-4 text-xs text-muted">
          本地模式：API 未配置 AUTH_SECRET，治理端点对单用户放行（数据仅保存在本机）。
        </Card>
      )}
      {showPanel && (
        <>
          <GovTabs tabs={GOV_TABS.map((entry) => ({ ...entry }))} active={tab} onChange={setTab} />
          {/* 分支渲染而非数组索引：保持每个队列的具体泛型类型 */}
          {tab === "import" && <DraftQueue config={importQueue} />}
          {tab === "generation" && <DraftQueue config={generationQueue} />}
          {tab === "paper" && <DraftQueue config={paperQueue} />}
          {tab === "variant" && <DraftQueue config={variantQueue} />}
          {tab === "audit" && <AuditLog />}
        </>
      )}
    </div>
  );
}

function PageHeader() {
  return (
    <div>
      <p className="text-xs text-muted">治理</p>
      <h1 className="mt-1 font-display text-3xl">治理工作台</h1>
      <p className="mt-2 max-w-xl text-sm text-muted">
        四类内容草稿（课程导入 / 课程生成 / 试卷抽取 / 变式题）的人工审核与审计回查；决定权在人，系统只负责留痕。
      </p>
    </div>
  );
}
