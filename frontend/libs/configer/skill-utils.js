async function openSkillMdModal(slug, content, agentName) {
  const area = el("div");
  area.style.height = "360px";
  area.style.border = "1px solid var(--line)";
  area.style.borderRadius = "10px";
  area.style.overflow = "hidden";
  const info = el("div", "card-help", `Skill: ${slug} | Agent: ${agentName || "-"}`);
  const bar = el("div", "toolbar");
  let editor = null;
  bar.append(
    makeButton("保存 SKILL.md", async () => {
      const useAgent = String(agentName || state.skillsAgent || "").trim();
      await cfgApi("PUT", `/faust/admin/skills/${encodeURIComponent(slug)}/skill-md`, {
        agent_name: useAgent || null,
        content: editor ? editor.getValue() : "",
      });
      if (state.skillDetail && String(state.skillDetail.slug || "") === String(slug)) {
        state.skillDetail.skill_md = editor ? editor.getValue() : "";
      }
      showBanner("success", `SKILL.md 已保存: ${slug}`);
      closeModal();
      await ensureModuleData("skills");
      renderModule();
    }, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );
  openModal(`编辑 SKILL.md - ${slug}`, [info, area, bar]);
  editor = await createCodeMirrorEditor(area, String(content || ""), {
    language: "markdown",
    readOnly: false,
  });
}
