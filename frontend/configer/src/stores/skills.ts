import { ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import type { SkillDetail, SkillRecord } from '@/modules/skills/skillUtils';

/** 技能页状态，对应旧 state.skills / skillsAgent / selectedSkillSlug / skillDetail。 */
export const useSkillsStore = defineStore('configer-skills', () => {
  const skills = ref<SkillRecord[]>([]);
  const selectedSkillSlug = ref('');
  const skillsAgent = ref('');
  const skillDetail = ref<SkillDetail | null>(null);

  /**
   * 旧 ensureModuleData('skills')：列表 + 选中项详情。
   * 单个技能详情失败不清空列表；列表失败则清空列表（旧实现如此）。
   */
  async function loadSkills(): Promise<void> {
    try {
      const agentName = skillsAgent.value || '';
      const params = agentName ? { agent_name: agentName } : {};
      const data = await cfgApi<{ items?: SkillRecord[] }>('GET', API.skills, null, params);
      skills.value = data.items || [];
      skillDetail.value = null;
      if (selectedSkillSlug.value) {
        try {
          const detail = await cfgApi<{ detail?: SkillDetail }>(
            'GET',
            API.skill(selectedSkillSlug.value),
            null,
            params,
          );
          skillDetail.value = detail.detail || null;
        } catch (err) {
          console.warn('skill detail fetch error', err);
          skillDetail.value = null;
        }
      }
    } catch (err) {
      console.warn('skills data fetch error', err);
      skills.value = [];
    }
  }

  async function installSlug(slug: string, agentName: string, overwrite: boolean): Promise<void> {
    await cfgApi('POST', API.skillInstall, { slug, agent_name: agentName, overwrite });
  }

  async function installZip(
    zipPath: string,
    agentName: string,
    overwrite: boolean,
  ): Promise<void> {
    await cfgApi('POST', API.skillInstallZip, {
      zip_path: zipPath,
      agent_name: agentName,
      overwrite,
    });
  }

  async function deleteSkill(slug: string): Promise<void> {
    await cfgApi('DELETE', API.skill(slug), null, { agent_name: skillsAgent.value });
  }

  /** 旧 openSkillMdModal 的保存：顺带把详情里的 skill_md 就地更新。 */
  async function saveSkillMd(slug: string, agentName: string, content: string): Promise<void> {
    await cfgApi('PUT', API.skillMd(slug), {
      agent_name: agentName || null,
      content,
    });
    if (skillDetail.value && String(skillDetail.value.slug || '') === String(slug)) {
      skillDetail.value.skill_md = content;
    }
  }

  return {
    skills,
    selectedSkillSlug,
    skillsAgent,
    skillDetail,
    loadSkills,
    installSlug,
    installZip,
    deleteSkill,
    saveSkillMd,
  };
});
