import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  studioGet,
  studioWrite,
  studioUpload,
  studioAsset,
  hasStudioCredential,
} from "../../../api";
import StudioConcept, { Recipe, Concept } from "./StudioConcept";
import "./studio.css";
const messages: Record<string, string> = {
  queued: "排队中",
  running: "正在处理",
  submitted: "视频生成中",
  succeeded: "完成",
  failed: "失败",
  submission_unknown: "提交结果待核查，禁止重复生成",
  cancelled: "已取消",
  blocked: "权限已撤销",
};
function errorText(e: any) {
  if (e.response?.status === 404) return "创意服务暂未连接，请检查左侧账号管理中的运营凭证后重试。";
  return e.response?.data?.error?.message || e.message || "操作失败";
}
function AssetPreview({
  id,
  mime = "video/mp4",
}: {
  id: string;
  mime?: string;
}) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true,
      u = "";
    studioAsset(id)
      .then((blob) => {
        u = URL.createObjectURL(blob);
        if (active) setUrl(u);
        else URL.revokeObjectURL(u);
      })
      .catch(() => {});
    return () => {
      active = false;
      if (u) URL.revokeObjectURL(u);
    };
  }, [id]);
  return url ? (
    <div>
      {mime.startsWith("image/") ? (
        <img src={url} alt="商家上传的参考素材" />
      ) : (
        <video controls src={url} />
      )}
      <a
        href={url}
        download={id + (mime.startsWith("image/") ? ".png" : ".mp4")}
      >
        下载素材
      </a>
    </div>
  ) : (
    <p>素材正在加载或当前无权读取</p>
  );
}
export default function MerchantStudio() {
  const [caps, setCaps] = useState<any>(null),
    [brands, setBrands] = useState<any[]>([]),
    [brand, setBrand] = useState(""),
    [history, setHistory] = useState<any[]>([]);
  const [subject, setSubject] = useState(""),
    [type, setType] = useState("product"),
    [objective, setObjective] = useState("traffic"),
    [idea, setIdea] = useState(""),
    [facts, setFacts] = useState(""),
    [assets, setAssets] = useState<any[]>([]),
    [role, setRole] = useState("subject_photo");
  const [sessionId, setSessionId] = useState(""),
    [snapshot, setSnapshot] = useState<any>(null),
    [recipes, setRecipes] = useState<Recipe[]>([]),
    [outputs, setOutputs] = useState<any[]>([]),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [videoRecipe, setVideoRecipe] = useState<Recipe | null>(null),
    [duration, setDuration] = useState(5),
    [ratio, setRatio] = useState("9:16"),
    [firstFrame, setFirstFrame] = useState(""),
    [confirmed, setConfirmed] = useState(false);
  const [bidChannel, setBidChannel] = useState(""),
    [margin, setMargin] = useState(""),
    [probability, setProbability] = useState(""),
    [share, setShare] = useState("");
  const epoch = useRef(0);
  const analysisJobs=assets.filter(a=>a.data.analysis_job_id&&!['succeeded','failed','blocked','cancelled'].includes(a.analysis_status)).map(a=>a.data.analysis_job_id).join(',');
  useEffect(()=>{if(!analysisJobs)return;let cancelled=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const jobs=await Promise.all(analysisJobs.split(',').map(id=>studioGet('/jobs/'+id)));if(!cancelled)setAssets(previous=>previous.map(a=>{const job=jobs.find(j=>j.id===a.data.analysis_job_id);return job?{...a,analysis_status:job.status,analysis:job.result,analysis_error:job.error_code}:a;}));}catch(e){if(!cancelled)setError(errorText(e));}if(!cancelled)timer=setTimeout(poll,2000);};void poll();return()=>{cancelled=true;clearTimeout(timer);};
  },[analysisJobs]);

  const initialize = async () => {
    const generation = ++epoch.current;
    setError("");
    if (!hasStudioCredential()) {
      setError("请在左侧账号管理中设置运营凭证");
      return;
    }
    try {
      const [c, b, h] = await Promise.all([
        studioGet("/capabilities"),
        studioGet("/brands"),
        studioGet("/sessions"),
      ]);
      if (generation !== epoch.current) return;
      setCaps(c);
      setBrands(b);
      setBrand(b[0]?.id || "");
      setHistory(h);
    } catch (e) {
      if (generation === epoch.current) setError(errorText(e));
    }
  };
  useEffect(() => {
    const reconnect = () => {
      epoch.current++;
      setSessionId(""); setSnapshot(null); setRecipes([]); setOutputs([]);
      setAssets([]); setBrands([]); setBrand(""); setHistory([]); setCaps(null);
      setVideoRecipe(null); setNotice("");
      void initialize();
    };
    window.addEventListener("studio-credential-changed", reconnect);
    void initialize();
    return () => {
      window.removeEventListener("studio-credential-changed", reconnect);
      epoch.current++;
    };
  }, []);
  const refresh = useCallback(async () => {
    if (!sessionId) return;
    const gen = epoch.current;
    try {
      const data = await studioGet("/sessions/" + sessionId);
      const [rs, os] = await Promise.all([
        Promise.all(
          data.jobs
            .flatMap((j: any) => j.result?.recipe_ids || [])
            .map((id: string) => studioGet("/recipes/" + id)),
        ),
        Promise.all(
          data.jobs
            .filter((j: any) => j.result?.output_id)
            .map((j: any) => studioGet("/outputs/" + j.result.output_id)),
        ),
      ]);
      if (gen !== epoch.current) return;
      setSnapshot(data);
      setRecipes(rs);
      setOutputs(os);
    } catch (e) {
      if (gen === epoch.current) setError(errorText(e));
    }
  }, [sessionId]);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      void refresh();
    }, 3000);
    return () => clearInterval(timer);
  }, [refresh]);
  const work = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const create = () =>
    work(async () => {
      const generation = epoch.current;
      for (const asset of assets) {
        const updated = await studioWrite('/assets/' + asset.id + '/analyze', {});
        setAssets(old => old.map(a => a.id === asset.id ? {...a, data: updated.data, analysis_status: 'queued'} : a));
        const deadline = Date.now() + 180000;
        while (true) {
          if (generation !== epoch.current) return;
          const job = await studioGet('/jobs/' + updated.data.analysis_job_id);
          if (job.status === 'succeeded') break;
          if (['failed','blocked','cancelled'].includes(job.status)) throw new Error('素材理解失败，请重试素材理解后再提交。');
          if (Date.now() > deadline) throw new Error('素材仍在理解，请等待完成后再次提交需求。');
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
      }
      if (generation !== epoch.current) return;
      const b = await studioWrite("/briefs", {
        brand_id: brand,
        subject_type: type,
        subject_description: subject,
        objective,
        idea,
        confirmed_facts: facts
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
        ...(bidChannel && margin && probability && share
          ? {
              bid_basis: {
                channel: bidChannel,
                contribution_minor: Math.round(Number(margin) * 100),
                purchase_probability: Number(probability) / 100,
                ad_share: Number(share) / 100,
              },
            }
          : {}),
        asset_refs: assets.map((a) => ({ asset_id: a.id, role: a.role })),
      });
      const s = await studioWrite("/sessions", {
        brief_id: b.id,
        brief_revision: b.revision,
      });
      epoch.current++;
      setRecipes([]);
      setOutputs([]);
      setSnapshot(null);
      setSessionId(s.id);
      await studioWrite("/sessions/" + s.id + "/turns", {
        expected_revision: s.revision,
        message:
          "请结合Seed素材理解结果与商家文字提出创意方向、文案与视频脚本，未经确认的素材宣称不要当作商品事实。",
      });
      setHistory(await studioGet("/sessions"));
    });
  const chat = () =>
    work(async () => {
      await studioWrite("/sessions/" + sessionId + "/turns", {
        expected_revision: snapshot.session.revision,
        message,
      });
      setMessage("");
      await refresh();
    });
  const decide = (r: Recipe, decision: string, concept?: Concept) =>
    void work(async () => {
      await studioWrite("/recipes/" + r.id + "/decisions", {
        expected_revision: r.revision,
        decision,
        reason:
          decision === "reject"
            ? "商家拒绝当前方向"
            : decision === "modify"
              ? "商家修改并确认"
              : "商家确认文案与事实",
        ...(concept ? { concept } : {}),
      });
      await refresh();
    });
  const generate = () =>
    work(async () => {
      if (!videoRecipe || !confirmed) return;
      await studioWrite("/recipes/" + videoRecipe.id + "/jobs", {
        expected_revision: videoRecipe.revision,
        output_type: "video",
        confirm_generation: true,
        spec: { duration, resolution: "480p", ratio },
        ...(firstFrame ? { asset_id: firstFrame } : {}),
      });
      setVideoRecipe(null);
      setConfirmed(false);
      await refresh();
    });
  const pending = snapshot?.jobs.some(
    (j: any) => ["queued", "running"].includes(j.status) && j.kind === "text",
  );
  return (
    <section className="studio-shell" aria-label="创意素材生成">
      <header className="studio-heading">
        <div>
          <h2>告诉我们要推广什么，一起打磨创意吧✨</h2>
        </div>
      </header>
      {error && (
        <p role="alert" className="studio-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <div className="studio-layout">
        <section className="studio-card studio-stack">
          <h2>1. 营销需求</h2>
          <label>
            品牌
            <select
              value={brand}
              onChange={(e) => {
                setBrand(e.target.value);
                setAssets([]);
              }}
            >
              {brands.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            主体类型
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {[
                ["product", "商品"],
                ["service", "服务"],
                ["store", "门店"],
                ["event", "活动"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            推广什么
            <textarea
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={4000}
              placeholder="例如：一根白色牙刷，面向通勤人群"
            />
          </label>
          <label>
            推广目标
            <select
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
            >
              {[
                ["awareness", "提高知名度"],
                ["traffic", "访问"],
                ["leads", "咨询"],
                ["appointments", "预约"],
                ["sales", "成交"],
                ["undecided", "一起讨论"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            创意想法（选填）
            <textarea
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              maxLength={4000}
              placeholder="想表现什么场景、采用什么风格？"
            />
          </label>
          <label>
            你确认的商品事实（每行一条）
            <textarea
              value={facts}
              onChange={(e) => setFacts(e.target.value)}
              maxLength={10000}
              placeholder="例如真实售价、已确认材质。不要填写猜测。"
            />
          </label>
          <label>
            附件用途
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="subject_photo">营销主体照片</option>
              <option value="existing_ad">已有广告</option>
              <option value="style_reference">风格参考</option>
            </select>
          </label>
          <div className="studio-upload-group">
            <span className="studio-upload-title">图片或视频附件</span>
            <div className="studio-upload-options">
              {[
                { text: "上传图片或视频", accept: "image/jpeg,image/png,image/webp,video/mp4" },
              ].map(({ text, accept }) => (
                <label key={text} className={`video-upload-btn studio-upload-tile${busy || !brand || assets.length >= 5 ? " is-disabled" : ""}`}>
                  <span className="upload-icon" aria-hidden="true">+</span>
                  <span className="upload-text">{text}</span>
                  <input type="file" aria-label={text} accept={accept}
                    disabled={busy || !brand || assets.length >= 5}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      void work(async () => {
                        const a = await studioUpload(brand, f);
                        setAssets((old) => [...old, { ...a, role, analysis_status: 'not_started' }]);
                      });
                      e.target.value = "";
                    }} />
                </label>
              ))}
            </div>
          </div>
          <p className="studio-warning">
            点击「保存需求并讨论创意」后才分析商品、画面和营销信息。视频仅分析抽样画面，不包含音频；上传不会触发视频生成。
          </p>
          {assets.map((a) => (
            <div key={a.id} className="studio-actions">
              <span>
                {a.data.mime} · {Math.round(a.data.size / 1024)} KiB
              </span>
              <button
                onClick={() => setAssets(assets.filter((x) => x.id !== a.id))}
              >
                移除引用
              </button>
              <div className="studio-analysis" role="status">
                <strong>{a.analysis_status==='not_started'?'已上传，提交需求后开始理解':a.analysis_status==='succeeded'?'素材理解完成':a.analysis_status==='failed'?(a.analysis_error==='seed_model_not_open'?'Seed模型尚未开通，请配置已开通的理解模型':'素材理解失败'):a.analysis_status==='blocked'?'无权读取素材':'正在理解素材…'}</strong>
                {a.analysis&&<><p>{a.analysis.subject}</p><p>{a.analysis.marketing_summary}</p><details><summary>查看识别依据与不确定信息</summary>{a.analysis.observations?.map((o:any,i:number)=><p key={i}>{o.time_ms!==null?'['+(o.time_ms/1000).toFixed(1)+'秒] ':''}{o.description}</p>)}{a.analysis.source_claims?.length>0&&<p>素材宣称（未核验）：{a.analysis.source_claims.join('；')}</p>}{a.analysis.uncertainties?.length>0&&<p>不确定：{a.analysis.uncertainties.join('；')}</p>}{a.analysis.limitations?.map((l:string)=><p key={l}>{l}</p>)}</details></>}
                {a.analysis_status==='failed'&&<button onClick={()=>void work(async()=>{await studioWrite('/jobs/'+a.data.analysis_job_id+'/retry',{confirm_retry:true});setAssets(old=>old.map(x=>x.id===a.id?{...x,analysis_status:'queued',analysis:null}:x));})}>重试素材理解</button>}
              </div>
            </div>
          ))}
          <button
            disabled={
              busy ||
              !caps?.enabled ||
              !brand ||
              !!analysisJobs ||
              (!subject.trim() && !idea.trim() && !assets.length)
            }
            onClick={create}
          >
            保存需求并讨论创意
          </button>
          <h3>最近会话</h3>
          {history.map((h) => (
            <button
              key={h.id}
              onClick={() => {
                epoch.current++;
                setSessionId(h.id);
                setSnapshot(null);
                setRecipes([]);
                setOutputs([]);
                setVideoRecipe(null);
              }}
            >
              {h.id.slice(0, 8)} · {new Date(h.created_at).toLocaleString()}
            </button>
          ))}
        </section>
        <section className="studio-stack studio-workspace">
          <h2>2. 对话与创意方案</h2>
          {!sessionId && <div className="studio-empty"><span aria-hidden="true">✦</span><h3>从一个推广想法开始</h3><p>填写营销需求，点击「保存需求并讨论创意」。<br />助手会整理卖点和脚本，再由你确认生成视频。</p><ol><li>填写需求</li><li>确认创意</li><li>生成视频</li></ol></div>}
          {snapshot && (
            <p className="studio-muted">
              需求版本 {snapshot.session.data.brief_revision} ·{" "}
              {snapshot.brief.data.subject_description}
            </p>
          )}
          {snapshot?.session.data.turns.map((t: any) => (
            <article key={t.job_id} className="studio-card">
              <p>
                <strong>商家：</strong>
                {t.message}
              </p>
              {(() => {
                const j = snapshot.jobs.find((x: any) => x.id === t.job_id);
                return (
                  <>
                    <p role="status">
                      {messages[j?.status] || "待处理"}
                      {j?.error_code ? " · " + j.error_code : ""}
                    </p>
                    {j?.result?.summary && (
                      <>
                        <p>{j.result.summary}</p>
                        {j.result.questions?.length > 0 && (
                          <p>需要补充：{j.result.questions.join("；")}</p>
                        )}
                      </>
                    )}
                  </>
                );
              })()}
            </article>
          ))}
          {sessionId && (
            <form
              className="studio-card studio-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void chat();
              }}
            >
              <label>
                继续讨论
                <textarea
                  value={message}
                  maxLength={2000}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="说明真实卖点、要求改写风格或调整场景"
                />
              </label>
              <button
                disabled={busy || pending || !snapshot || !message.trim()}
              >
                发送给创意助手
              </button>
            </form>
          )}
          {recipes.map((r) => (
            <StudioConcept
              key={r.id + ":" + r.revision}
              recipe={r}
              onDecide={decide}
              onSave={(r) =>
                void work(async () => {
                  await studioWrite("/recipes/" + r.id + "/save-draft", {
                    expected_revision: r.revision,
                    title: r.data.concept.title,
                  });
                  setNotice("文案草稿已保存，尚未发布广告");
                })
              }
              onImage={r => void work(async () => {
                await studioWrite('/recipes/' + r.id + '/image', {});
                setNotice('图片生成已提交，完成后会显示在素材结果中。');
                await refresh();
              })}
              onVideo={(r) => {
                setVideoRecipe(r);
                setConfirmed(false);
                setFirstFrame("");
              }}
              busy={busy}
              videoEnabled={!!caps?.video}
            />
          ))}
        </section>
      </div>
      {videoRecipe && (
        <section className="studio-card studio-stack">
          <h2>3. 确认视频生成</h2>
          <p>{videoRecipe.data.concept.video_prompt}</p>
          <div className="studio-actions">
            <label>
              时长（秒）
              <input
                type="number"
                min={2}
                max={12}
                value={duration}
                onChange={(e) => setDuration(Number(e.target.value))}
              />
            </label>
            <label>
              比例
              <select value={ratio} onChange={(e) => setRatio(e.target.value)}>
                <option>9:16</option>
                <option>16:9</option>
                <option>1:1</option>
              </select>
            </label>
            <label>
              首帧
              <select
                value={firstFrame}
                onChange={(e) => setFirstFrame(e.target.value)}
              >
                <option value="">文生视频，无首帧</option>
                {(snapshot?.brief.data.asset_refs || []).map((a: any) => (
                  <option key={a.asset_id} value={a.asset_id}>
                    {a.asset_id.slice(0, 8)}（仅图片可用）
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="studio-warning">
            生成会调用 Seedance
            并可能产生方舟费用；480p，开启水印，不含口播。超时先查单，不能重复提交。
          </p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我确认以上方案、规格并授权此次付费视频生成
          </label>
          <div className="studio-actions">
            <button disabled={busy || !confirmed} onClick={generate}>
              确认生成一次视频
            </button>
            <button onClick={() => setVideoRecipe(null)}>返回修改</button>
          </div>
        </section>
      )}
      {snapshot?.jobs
        .filter((j: any) => j.kind === "video")
        .map((j: any) => (
          <section className="studio-card" key={j.id}>
            <h2>视频任务</h2>
            <p role="status">
              {messages[j.status]} · {j.error_code || ""}
            </p>
            <p className="studio-muted">
              任务 {j.id}
              {j.provider_task_id ? " · 方舟 " + j.provider_task_id : ""}
            </p>
            {j.status === "queued" && (
              <button
                disabled={busy}
                onClick={() =>
                  void work(async () => {
                    await studioWrite("/jobs/" + j.id + "/cancel", {
                      confirm_cancel: true,
                    });
                    await refresh();
                  })
                }
              >
                取消尚未提交的任务
              </button>
            )}
            {j.result?.asset_id && <AssetPreview id={j.result.asset_id} />}
          </section>
        ))}
      {outputs.map((o) => (
        <section className="studio-card" key={o.id}>
          <h3>素材审核</h3>
          <p>
            状态：
            {o.data.review === "approve"
              ? "已通过"
              : o.data.review === "reject"
                ? "已拒绝"
                : "待审核"}
          </p>
          <div className="studio-actions">
            {["approve", "reject"].map((d) => (
              <button
                key={d}
                disabled={busy}
                onClick={() =>
                  void work(async () => {
                    await studioWrite("/outputs/" + o.id + "/review", {
                      expected_revision: o.revision,
                      decision: d,
                      reason: "商家审核",
                    });
                    await refresh();
                  })
                }
              >
                {d === "approve" ? "审核通过" : "拒绝素材"}
              </button>
            ))}
            <button
              disabled={busy || o.data.review !== "approve"}
              onClick={() =>
                void work(async () => {
                  await studioWrite("/outputs/" + o.id + "/save-draft", {
                    expected_revision: o.revision,
                    title: "视频创意草稿",
                  });
                  setNotice("草稿已保存，尚未发布广告");
                })
              }
            >
              保存未发布草稿
            </button>
          </div>
        </section>
      ))}
    </section>
  );
}
