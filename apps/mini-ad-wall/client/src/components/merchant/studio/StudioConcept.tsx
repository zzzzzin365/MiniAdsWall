import React, { useState } from "react";
export interface Concept {
  title: string;
  scenario: string;
  copy: string;
  script: string;
  video_prompt: string;
  assumptions: string[];
}
export interface Recipe {
  id: string;
  revision: number;
  data: { concept: Concept; decision: string };
}
export default function StudioConcept({
  recipe,
  onDecide,
  onVideo,
  onImage,
  onSave,
  busy,
  videoEnabled,
}: {
  recipe: Recipe;
  onDecide: (recipe: Recipe, decision: string, concept?: Concept) => void;
  onVideo: (recipe: Recipe) => void;
  onImage: (recipe: Recipe) => void;
  onSave: (recipe: Recipe) => void;
  busy: boolean;
  videoEnabled: boolean;
}) {
  const [editing, setEditing] = useState(false),
    [draft, setDraft] = useState(recipe.data.concept);
  return (
    <article className="studio-card">
      <h3>{recipe.data.concept.title}</h3>
      <p className="studio-muted">场景：{recipe.data.concept.scenario}</p>
      {editing ? (
        <div className="studio-stack">
          {(["title", "copy", "script", "video_prompt"] as const).map((k) => (
            <label key={k}>
              {
                {
                  title: "标题",
                  copy: "文案",
                  script: "脚本",
                  video_prompt: "视频画面提示词",
                }[k]
              }
              <textarea
                value={draft[k]}
                maxLength={
                  k === "video_prompt"
                    ? 3000
                    : k === "script"
                      ? 2000
                      : k === "copy"
                        ? 1000
                        : 120
                }
                onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
              />
            </label>
          ))}
          <button
            disabled={busy}
            onClick={() => {
              onDecide(recipe, "modify", draft);
              setEditing(false);
            }}
          >
            保存修改并确认
          </button>
        </div>
      ) : (
        <>
          <p>{recipe.data.concept.copy}</p>
          <details>
            <summary>脚本与视频画面</summary>
            <p className="studio-pre">{recipe.data.concept.script}</p>
            <p className="studio-pre">{recipe.data.concept.video_prompt}</p>
          </details>
        </>
      )}
      {!!recipe.data.concept.assumptions.length && (
        <p className="studio-warning">
          待验证：{recipe.data.concept.assumptions.join("；")}
        </p>
      )}
      <p>
        方案状态：
        {{
          pending: "待确认",
          accept: "已确认",
          modify: "修改后确认",
          reject: "已拒绝",
        }[recipe.data.decision] || recipe.data.decision}
      </p>
      <div className="studio-actions">
        <button disabled={busy || !["accept", "modify"].includes(recipe.data.decision)} onClick={() => onImage(recipe)}>生成图片素材</button>
        <button disabled={busy} onClick={() => onDecide(recipe, "accept")}>
          确认方案与事实
        </button>
        <button
          disabled={busy}
          onClick={() => {
            setDraft(recipe.data.concept);
            setEditing(!editing);
          }}
        >
          修改
        </button>
        <button disabled={busy} onClick={() => onDecide(recipe, "reject")}>
          拒绝
        </button>
        <button
          disabled={
            busy ||
            !videoEnabled ||
            !["accept", "modify"].includes(recipe.data.decision)
          }
          onClick={() => onVideo(recipe)}
        >
          准备生成视频
        </button>
        <button
          disabled={
            busy || !["accept", "modify"].includes(recipe.data.decision)
          }
          onClick={() => onSave(recipe)}
        >
          保存文案草稿
        </button>
      </div>
    </article>
  );
}
