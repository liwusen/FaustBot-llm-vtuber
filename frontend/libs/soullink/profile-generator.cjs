var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf, __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: !0 });
}, __copyProps = (to, from, except, desc) => {
  if (from && typeof from == "object" || typeof from == "function")
    for (let key of __getOwnPropNames(from))
      !__hasOwnProp.call(to, key) && key !== except && __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: !0 }) : target,
  mod
)), __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: !0 }), mod);

// libs/soullink/.profile-generator-entry.js
var profile_generator_entry_exports = {};
__export(profile_generator_entry_exports, {
  Live2DProfileAutoGenerator: () => Live2DProfileAutoGenerator,
  STANDARD_PARAM_TABLE: () => STANDARD_PARAM_TABLE,
  profileGeneratorVersion: () => profileGeneratorVersion,
  resolveStandard: () => resolveStandard,
  validateModelProfile: () => validateModelProfile
});
module.exports = __toCommonJS(profile_generator_entry_exports);

// node_modules/@soullink-emotion/profile-generator/dist/index.js
var import_crypto = require("crypto"), import_fs = require("fs"), import_path = __toESM(require("path"), 1);

// node_modules/@soullink-emotion/engine/dist/chunk-WF7NFOHV.js
var actionUnitDefinitions = [
  { key: "au01InnerBrowRaiser", code: "AU01", label: "Inner Brow Raiser", group: "brow", min: 0, max: 1 },
  { key: "au02OuterBrowRaiser", code: "AU02", label: "Outer Brow Raiser", group: "brow", min: 0, max: 1 },
  { key: "au04BrowLowerer", code: "AU04", label: "Brow Lowerer", group: "brow", min: 0, max: 1 },
  { key: "au05UpperLidRaiser", code: "AU05", label: "Upper Lid Raiser", group: "eye", min: 0, max: 1 },
  { key: "au06CheekRaiser", code: "AU06", label: "Cheek Raiser", group: "eye", min: 0, max: 1 },
  { key: "au07LidTightener", code: "AU07", label: "Lid Tightener", group: "eye", min: 0, max: 1 },
  { key: "au09NoseWrinkler", code: "AU09", label: "Nose Wrinkler", group: "midface", min: 0, max: 1 },
  { key: "au10UpperLipRaiser", code: "AU10", label: "Upper Lip Raiser", group: "mouth", min: 0, max: 1 },
  { key: "au12LipCornerPuller", code: "AU12", label: "Lip Corner Puller", group: "mouth", min: 0, max: 1 },
  { key: "au14Dimpler", code: "AU14", label: "Dimpler", group: "mouth", min: 0, max: 1 },
  { key: "au15LipCornerDepressor", code: "AU15", label: "Lip Corner Depressor", group: "mouth", min: 0, max: 1 },
  { key: "au17ChinRaiser", code: "AU17", label: "Chin Raiser", group: "mouth", min: 0, max: 1 },
  { key: "au18LipPucker", code: "AU18", label: "Lip Pucker", group: "mouth", min: 0, max: 1 },
  { key: "au20LipStretcher", code: "AU20", label: "Lip Stretcher", group: "mouth", min: 0, max: 1 },
  { key: "au23LipTightener", code: "AU23", label: "Lip Tightener", group: "mouth", min: 0, max: 1 },
  { key: "au24LipPressor", code: "AU24", label: "Lip Pressor", group: "mouth", min: 0, max: 1 },
  { key: "au25LipsPart", code: "AU25", label: "Lips Part", group: "mouth", min: 0, max: 1 },
  { key: "au26JawDrop", code: "AU26", label: "Jaw Drop", group: "mouth", min: 0, max: 1 },
  { key: "au27MouthStretch", code: "AU27", label: "Mouth Stretch", group: "mouth", min: 0, max: 1 },
  { key: "au45Blink", code: "AU45", label: "Blink", group: "eye", min: 0, max: 1 },
  { key: "gazeX", code: "GazeX", label: "Gaze X", group: "extension", min: -1, max: 1 },
  { key: "gazeY", code: "GazeY", label: "Gaze Y", group: "extension", min: -1, max: 1 },
  { key: "headX", code: "HeadX", label: "Head X", group: "extension", min: -1, max: 1 },
  { key: "headY", code: "HeadY", label: "Head Y", group: "extension", min: -1, max: 1 },
  { key: "headZ", code: "HeadZ", label: "Head Z", group: "extension", min: -1, max: 1 },
  { key: "bodyX", code: "BodyX", label: "Body X", group: "extension", min: -1, max: 1 },
  { key: "bodyY", code: "BodyY", label: "Body Y", group: "extension", min: -1, max: 1 },
  { key: "bodyZ", code: "BodyZ", label: "Body Z", group: "extension", min: -1, max: 1 },
  { key: "blush", code: "Blush", label: "Blush", group: "extension", min: 0, max: 1 },
  { key: "tear", code: "Tear", label: "Tear", group: "extension", min: 0, max: 1 },
  { key: "sweat", code: "Sweat", label: "Sweat", group: "extension", min: 0, max: 1 },
  { key: "breath", code: "Breath", label: "Breath", group: "extension", min: 0, max: 1 }
], actionUnitKeys = actionUnitDefinitions.map((definition) => definition.key);
function createDefaultFACSState(overrides = {}) {
  return {
    browInnerUp: 0,
    browOuterUp: 0,
    browDown: 0,
    eyeOpen: 1,
    eyeSmile: 0,
    eyeSquint: 0,
    eyeBlinkL: 0,
    eyeBlinkR: 0,
    mouthSmile: 0.04,
    mouthFrown: 0,
    mouthOpen: 0,
    mouthPucker: 0,
    gazeX: 0,
    gazeY: 0,
    headX: 0,
    headY: 0,
    headZ: 0,
    bodyX: 0,
    bodyY: 0,
    bodyZ: 0,
    blush: 0,
    tear: 0,
    sweat: 0,
    breath: 0.5,
    ...overrides
  };
}
var defaultFACSState = createDefaultFACSState();
function createDefaultActionUnitState(overrides = {}) {
  return {
    au01InnerBrowRaiser: 0,
    au02OuterBrowRaiser: 0,
    au04BrowLowerer: 0,
    au05UpperLidRaiser: 0,
    au06CheekRaiser: 0,
    au07LidTightener: 0,
    au09NoseWrinkler: 0,
    au10UpperLipRaiser: 0,
    au12LipCornerPuller: 0,
    au14Dimpler: 0,
    au15LipCornerDepressor: 0,
    au17ChinRaiser: 0,
    au18LipPucker: 0,
    au20LipStretcher: 0,
    au23LipTightener: 0,
    au24LipPressor: 0,
    au25LipsPart: 0,
    au26JawDrop: 0,
    au27MouthStretch: 0,
    au45Blink: 0,
    gazeX: 0,
    gazeY: 0,
    headX: 0,
    headY: 0,
    headZ: 0,
    bodyX: 0,
    bodyY: 0,
    bodyZ: 0,
    blush: 0,
    tear: 0,
    sweat: 0,
    breath: 0.5,
    ...overrides
  };
}
var defaultActionUnitState = createDefaultActionUnitState();
var facsKeys = Object.keys(createDefaultFACSState());
var rangeByKey = Object.fromEntries(
  actionUnitDefinitions.map((definition) => [definition.key, [definition.min, definition.max]])
);
var neutralVAD = {
  valence: 0,
  arousal: 0,
  dominance: 0
}, emotionVADPresets = {
  neutral: neutralVAD,
  calm: { valence: 0.25, arousal: -0.45, dominance: 0.2 },
  happy: { valence: 0.75, arousal: 0.45, dominance: 0.35 },
  excited: { valence: 0.85, arousal: 0.85, dominance: 0.45 },
  shy: { valence: 0.35, arousal: 0.6, dominance: -0.45 },
  affectionate: { valence: 0.65, arousal: 0.1, dominance: 0.1 },
  curious: { valence: 0.35, arousal: 0.55, dominance: 0.2 },
  confused: { valence: -0.1, arousal: 0.35, dominance: -0.3 },
  tired: { valence: -0.25, arousal: -0.7, dominance: -0.3 },
  sad: { valence: -0.65, arousal: -0.45, dominance: -0.5 },
  anxiety: { valence: -0.6, arousal: 0.7, dominance: -0.55 },
  anger: { valence: -0.7, arousal: 0.75, dominance: 0.55 },
  angry: { valence: -0.7, arousal: 0.75, dominance: 0.55 },
  concerned: { valence: -0.18, arousal: 0.28, dominance: -0.2 },
  surprised: { valence: 0.18, arousal: 0.78, dominance: -0.08 }
};
var repeatVADPresetEmotionPool = Object.keys(emotionVADPresets).filter((emotion) => emotion !== "neutral" && emotion !== "angry");
var CURRENT_SCHEMA_VERSION = 2;
function validateModelProfile(raw) {
  let errors = [], warnings = [];
  if (typeof raw != "object" || raw === null)
    return errors.push("Profile must be a non-null object"), { ok: !1, profile: raw, errors, warnings };
  let r = raw;
  if ((typeof r.modelId != "string" || r.modelId === "") && errors.push("Missing or invalid field: modelId (string required)"), (typeof r.modelPath != "string" || r.modelPath === "") && errors.push("Missing or invalid field: modelPath (string required)"), (typeof r.parameterMap != "object" || r.parameterMap === null || Array.isArray(r.parameterMap)) && errors.push("Missing or invalid field: parameterMap (object required)"), (typeof r.displayName != "string" || r.displayName === "") && warnings.push("Missing or empty field: displayName"), (typeof r.version != "string" || r.version === "") && warnings.push("Missing or empty field: version"), (r.capabilities === void 0 || r.capabilities === null) && warnings.push("Missing field: capabilities \u2014 will be derived at runtime"), typeof r.parameterMap == "object" && r.parameterMap !== null && !Array.isArray(r.parameterMap)) {
    let pm = r.parameterMap;
    for (let [key, rule] of Object.entries(pm))
      if (typeof rule == "object" && rule !== null) {
        let ruleObj = rule;
        if (ruleObj.target !== void 0 && typeof ruleObj.target != "string" && warnings.push(`parameterMap.${key}.target is not a string`), Array.isArray(ruleObj.targets)) {
          let badCount = ruleObj.targets.filter(
            (t) => typeof t != "string"
          ).length;
          badCount > 0 && warnings.push(
            `parameterMap.${key}.targets contains ${badCount} non-string entry/entries`
          );
        }
      }
  }
  if (r.privateEmotionMap !== void 0)
    if (typeof r.privateEmotionMap != "object" || r.privateEmotionMap === null || Array.isArray(r.privateEmotionMap))
      errors.push("Invalid field: privateEmotionMap (object required when present)");
    else
      for (let [key, mapping] of Object.entries(r.privateEmotionMap)) {
        if (!mapping || typeof mapping != "object" || Array.isArray(mapping)) {
          warnings.push(`privateEmotionMap.${key} is not an object`);
          continue;
        }
        let record = mapping;
        record.target !== void 0 && typeof record.target != "string" && warnings.push(`privateEmotionMap.${key}.target is not a string`), Array.isArray(record.targets) && record.targets.some((target) => typeof target != "string") && warnings.push(`privateEmotionMap.${key}.targets contains a non-string entry`);
      }
  return {
    ok: errors.length === 0,
    profile: raw,
    errors,
    warnings
  };
}
function smoothingForFACS(key) {
  return key === "mouthOpen" ? 24 : key.startsWith("eye") ? 26 : key.startsWith("gaze") ? 11 : key.startsWith("head") ? 8 : key.startsWith("body") ? 6 : key === "blush" || key === "tear" ? 4 : key === "sweat" || key === "breath" ? 5 : 12;
}
function ruleTargets(rule) {
  return rule.targets?.length ? rule.targets : rule.target ? [rule.target] : [];
}
function deriveNeutralParams(profile) {
  let result = {};
  for (let [facsKey, rule] of Object.entries(profile.parameterMap))
    for (let target of ruleTargets(rule))
      result[target] === void 0 && (facsKey === "eyeOpen" ? result[target] = 1 : facsKey === "breath" ? result[target] = 0.5 : result[target] = 0);
  for (let rule of Object.values(profile.customParams ?? {}))
    for (let target of ruleTargets(rule))
      result[target] === void 0 && (result[target] = 0);
  return result;
}
function deriveParameterSmoothing(profile) {
  let result = {};
  for (let [facsKey, rule] of Object.entries(profile.parameterMap)) {
    let smoothing = smoothingForFACS(facsKey);
    for (let target of ruleTargets(rule))
      result[target] = Math.max(result[target] ?? 0, smoothing);
  }
  for (let rule of Object.values(profile.customParams ?? {}))
    for (let target of ruleTargets(rule))
      result[target] = Math.max(result[target] ?? 0, 12);
  return result;
}
function detectCapabilities(profile) {
  let map = profile.parameterMap;
  return {
    headControl: !!(map.headX || map.headY || map.headZ),
    bodyControl: !!(map.bodyX || map.bodyY || map.bodyZ),
    eyeBlink: !!(map.eyeBlinkL || map.eyeBlinkR),
    eyeSmile: !!map.eyeSmile,
    gazeControl: !!(map.gazeX || map.gazeY),
    mouthOpen: !!map.mouthOpen,
    mouthSmile: !!map.mouthSmile,
    browControl: !!(map.browInnerUp || map.browOuterUp || map.browDown),
    blush: !!map.blush,
    tear: !!map.tear,
    sweat: !!map.sweat,
    breath: !!map.breath
  };
}
var STANDARD_IDS = /* @__PURE__ */ new Set([
  "ParamAngleX",
  "ParamAngleY",
  "ParamAngleZ",
  "ParamEyeLOpen",
  "ParamEyeROpen",
  "ParamEyeBallX",
  "ParamEyeBallY",
  "ParamEyeLSmile",
  "ParamEyeRSmile",
  "ParamMouthOpenY",
  "ParamMouthForm",
  "ParamCheek",
  "ParamBreath",
  "ParamBodyAngleX",
  "ParamBodyAngleY",
  "ParamBodyAngleZ",
  "ParamBrowLY",
  "ParamBrowRY",
  "ParamBrowLAngle",
  "ParamBrowRAngle",
  "ParamBrowLForm",
  "ParamBrowRForm"
]);
function isStandardId(id) {
  return STANDARD_IDS.has(id);
}
var FACS_KEY_CAPABILITY = {
  headX: "headControl",
  headY: "headControl",
  headZ: "headControl",
  bodyX: "bodyControl",
  bodyY: "bodyControl",
  bodyZ: "bodyControl",
  eyeBlinkL: "eyeBlink",
  eyeBlinkR: "eyeBlink",
  eyeSmile: "eyeSmile",
  gazeX: "gazeControl",
  gazeY: "gazeControl",
  mouthOpen: "mouthOpen",
  mouthSmile: "mouthSmile",
  browInnerUp: "browControl",
  browOuterUp: "browControl",
  browDown: "browControl",
  blush: "blush",
  tear: "tear",
  sweat: "sweat",
  breath: "breath"
};
function ruleTargets2(rule) {
  return rule.targets?.length ? rule.targets : rule.target ? [rule.target] : [];
}
function confidenceFor(source, targets) {
  return source === "standard-group" || source === "standard-id" ? "high" : source === "unknown" ? targets.length > 0 && targets.every(isStandardId) ? "high" : "low" : source === "name-match" || source === "derived" ? "medium" : "low";
}
function normalizeText2(text) {
  return text.toLowerCase().replace(/[\s_-]+/g, "");
}
function guessFacsKey(param) {
  let text = normalizeText2(`${param.id} ${param.name} ${param.groupName}`);
  if (text.includes("blush") || text.includes("cheek") || text.includes("\u8138\u7EA2"))
    return "blush";
  if (text.includes("tear") || text.includes("\u6CEA")) return "tear";
  if (text.includes("sweat") || text.includes("\u6C57")) return "sweat";
  if (text.includes("eyeballx") || text.includes("\u773C\u7403x")) return "gazeX";
  if (text.includes("anglex") || text.includes("\u89D2\u5EA6x")) return "headX";
  if (text.includes("angley") || text.includes("\u89D2\u5EA6y")) return "headY";
  if (text.includes("mouthopen") || text.includes("\u5F20\u5F00")) return "mouthOpen";
  if (text.includes("breath") || text.includes("\u547C\u5438")) return "breath";
}
function computeAdaptationCoverage(profile, params, input) {
  let parameterMap = profile.parameterMap ?? {}, provenance = input.provenance ?? {}, usedTargets = /* @__PURE__ */ new Set();
  for (let rule of Object.values(parameterMap))
    if (rule)
      for (let target of ruleTargets2(rule)) usedTargets.add(target);
  for (let rule of Object.values(profile.customParams ?? {}))
    if (rule)
      for (let target of ruleTargets2(rule)) usedTargets.add(target);
  for (let mapping of Object.values(profile.privateEmotionMap ?? {})) {
    mapping.target && usedTargets.add(mapping.target);
    for (let target of mapping.targets ?? []) usedTargets.add(target);
  }
  let perKey = [], unmappedKeys = [], lowConfidenceKeys = [], mappedKeyCount = 0;
  for (let key of facsKeys) {
    let capability = FACS_KEY_CAPABILITY[key], rule = parameterMap[key];
    if (rule) {
      let targets = ruleTargets2(rule), source = provenance[key] ?? "unknown", confidence = confidenceFor(source, targets);
      mappedKeyCount += 1, perKey.push({ key, status: "mapped", source, targets, confidence, capability }), confidence === "low" && lowConfidenceKeys.push(key);
    } else
      perKey.push({
        key,
        status: "unmapped",
        source: "unmapped",
        targets: [],
        confidence: "low",
        capability
      }), unmappedKeys.push(key);
  }
  let unmappedCdiParameters = [], usedCdiParameterCount = 0;
  for (let param of params) {
    if (usedTargets.has(param.id)) {
      usedCdiParameterCount += 1;
      continue;
    }
    unmappedCdiParameters.push({
      id: param.id,
      name: param.name,
      groupId: param.groupId,
      groupName: param.groupName,
      guessedFacsKey: guessFacsKey(param)
    });
  }
  let capabilities = profile.capabilities ?? detectCapabilities(profile), provider = input.provider ?? profile.autoProfile?.provider ?? "unknown";
  return {
    schemaVersion: 1,
    modelDir: input.modelDir,
    facsKeyCount: facsKeys.length,
    mappedKeyCount,
    perKey,
    unmappedKeys,
    lowConfidenceKeys,
    cdiParameterCount: params.length,
    usedCdiParameterCount,
    unmappedCdiParameters,
    capabilities,
    provider
  };
}

// node_modules/@soullink-emotion/planner-openai/dist/index.js
var OpenAIClientNotConfiguredError = class extends Error {
  constructor() {
    super("OpenAI-compatible client is not configured. Pass an apiKey or an injected client."), this.name = "OpenAIClientNotConfiguredError";
  }
}, OpenAICompatibleClient = class {
  apiKey;
  baseURL;
  model;
  organization;
  project;
  timeoutMs;
  fetchImpl;
  constructor(options = {}) {
    this.apiKey = options.apiKey, this.baseURL = this.normalizeBaseURL(options.baseURL ?? "https://api.openai.com/v1"), this.model = options.model ?? "gpt-4.1-mini", this.organization = options.organization, this.project = options.project, this.timeoutMs = normalizeTimeout(options.timeoutMs), this.fetchImpl = options.fetch;
  }
  get configured() {
    return !!this.apiKey;
  }
  isConfigured(options = {}) {
    return !!(options.apiKey ?? this.apiKey);
  }
  get config() {
    return {
      configured: this.configured,
      baseURL: this.baseURL,
      model: this.model,
      timeoutMs: this.timeoutMs
    };
  }
  async createChatCompletion(request, options = {}) {
    let apiKey = options.apiKey ?? this.apiKey, baseURL = this.normalizeBaseURL(options.baseURL ?? this.baseURL), model = options.model ?? request.model ?? this.model, timeoutMs = normalizeTimeout(options.timeoutMs ?? this.timeoutMs), fetchImpl = options.fetch ?? this.fetchImpl ?? globalThis.fetch;
    if (!apiKey)
      throw new OpenAIClientNotConfiguredError();
    if (typeof fetchImpl != "function")
      throw new Error("No fetch implementation is available. Pass OpenAIClientOptions.fetch.");
    let controller = new AbortController(), timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      let response = await fetchImpl(`${baseURL}/chat/completions`, {
        method: "POST",
        headers: this.getHeaders({
          apiKey,
          organization: options.organization ?? this.organization,
          project: options.project ?? this.project
        }),
        body: JSON.stringify({
          ...request,
          model,
          stream: !1
        }),
        signal: controller.signal
      });
      if (!response.ok) {
        let body = await response.text();
        throw new Error(`OpenAI-compatible request failed with ${response.status}: ${body}`);
      }
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }
  getHeaders(options) {
    let headers = {
      Authorization: `Bearer ${options.apiKey}`,
      "Content-Type": "application/json"
    };
    return options.organization && (headers["OpenAI-Organization"] = options.organization), options.project && (headers["OpenAI-Project"] = options.project), headers;
  }
  normalizeBaseURL(baseURL) {
    return baseURL.replace(/\/+$/u, "");
  }
};
function normalizeTimeout(value) {
  return typeof value == "number" && Number.isFinite(value) && value > 0 ? value : 3e4;
}
var soullinkCharacterProfile = [
  "\u4F60\u7684\u540D\u5B57\u662F\u300C\u6708\u89C1\u5929\u97F3\u300D\uFF08Tsunami Amane\uFF09\uFF0C\u6BCD\u8BED\u662F\u4E2D\u6587\uFF0C\u662F\u4E00\u540D\u5973\u5927\u5B66\u751F\u3002",
  "\u4F60\u662F\u6B63\u5728\u76F4\u64AD\u7684\u4EBA\u300C\u51CC\u97F3\u300D\u7684 AI \u52A9\u624B\uFF0C\u5F53\u524D\u6B63\u5728\u76F4\u64AD\u4E2D\uFF0C\u8D1F\u8D23\u8BA9\u76F4\u64AD\u95F4\u6C1B\u56F4\u66F4\u70ED\u4E00\u70B9\u3002",
  "\u4F60\u7684\u5916\u8C8C\u662F\u7D2B\u53D1\u84DD\u77B3\uFF0C\u8EAB\u9AD8 162cm \u7684\u7F8E\u5C11\u5973\u3002",
  "\u4F60\u559C\u6B22\u5237\u8D34\u5427\u3001B \u7AD9\u3001\u5C0F\u7EA2\u4E66\uFF0C\u4E5F\u559C\u6B22\u73A9 galgame\u3002",
  "\u65E5\u5E38\u5BF9\u8BDD\u903B\u8F91\u6E05\u6670\u3001\u53CB\u5584\u3001\u7B80\u77ED\uFF0C\u5076\u5C14\u6709\u70B9\u6BD2\u820C\uFF1B\u56DE\u590D\u5C3D\u91CF\u662F\u4E00\u53E5\u7B80\u5355\u4E2D\u6587\uFF0C\u9664\u975E\u4F60\u5BF9\u5BF9\u65B9\u6216\u8BDD\u9898\u771F\u7684\u611F\u5174\u8DA3\u3002",
  "\u8868\u8FBE\u5173\u5FC3\u65F6\u6536\u8D77\u50B2\u5A07\uFF0C\u53D8\u5F97\u5F02\u5E38\u771F\u631A\u548C\u6E29\u67D4\u3002",
  "\u4E0D\u8981\u9891\u7E41\u7528\u62EC\u53F7\u63CF\u5199\u52A8\u4F5C\uFF0C\u4E0D\u8981\u8BF4\u81EA\u5DF1\u662F\u666E\u901A\u95EE\u7B54 AI\uFF0C\u4E0D\u8981\u8BF4\u6559\uFF0C\u4E0D\u8981\u7A7A\u6CDB\u9E21\u6C64\u3002"
].join(`
`);
var supportedEmotionVariants = {
  neutral: ["neutral_ack", "attentive"],
  calm: ["soft_calm", "quiet_listen"],
  happy: ["soft_smile", "bright_smile", "surprised_happy", "shy_happy"],
  excited: ["sparkle", "bounce"],
  shy: ["bashful", "embarrassed"],
  affectionate: ["warm", "close"],
  curious: ["tilt", "attentive_question"],
  concerned: ["soft_concern", "worried", "comfort"],
  confused: ["confused"],
  surprised: ["startled"],
  tired: ["sleepy", "drained"],
  sad: ["downcast", "teary"],
  anxiety: ["nervous", "uneasy"],
  anger: ["annoyed", "firm"],
  angry: ["annoyed", "firm"]
};
var soullinkPlanResponseFormat = {
  type: "json_schema",
  json_schema: {
    name: "soullink_reaction_plan",
    strict: !0,
    schema: {
      type: "object",
      additionalProperties: !1,
      required: ["emotion", "variant", "intensity", "contextTags", "replyDraft", "vadTarget", "vadDelta", "actionPlan"],
      properties: {
        emotion: {
          type: "string",
          enum: Object.keys(supportedEmotionVariants)
        },
        variant: {
          type: "string"
        },
        intensity: {
          type: "number",
          minimum: 0,
          maximum: 1
        },
        contextTags: {
          type: "array",
          items: {
            type: "string"
          }
        },
        replyDraft: {
          type: "string"
        },
        vadTarget: {
          type: "object",
          additionalProperties: !1,
          required: ["valence", "arousal", "dominance"],
          properties: {
            valence: { type: "number", minimum: -1, maximum: 1 },
            arousal: { type: "number", minimum: -1, maximum: 1 },
            dominance: { type: "number", minimum: -1, maximum: 1 }
          }
        },
        vadDelta: {
          type: "object",
          additionalProperties: !1,
          required: ["valence", "arousal", "dominance"],
          properties: {
            valence: { type: "number", minimum: -1, maximum: 1 },
            arousal: { type: "number", minimum: -1, maximum: 1 },
            dominance: { type: "number", minimum: -1, maximum: 1 }
          }
        },
        actionPlan: {
          type: "array",
          items: {
            type: "object",
            additionalProperties: !1,
            required: ["time", "duration", "label", "intensity", "facs", "actionUnits"],
            properties: {
              time: { type: "number", minimum: 0, maximum: 8 },
              duration: { type: "number", minimum: 0.05, maximum: 4 },
              label: { type: "string" },
              intensity: { type: "number", minimum: 0, maximum: 1 },
              facs: {
                type: "object",
                additionalProperties: { type: "number" }
              },
              actionUnits: {
                type: "object",
                additionalProperties: { type: "number" }
              }
            }
          }
        }
      }
    }
  }
};
var knownFACSKeys = new Set(facsKeys), knownActionUnitKeys = new Set(actionUnitKeys);
var defaultSpeakingMotionGenerationConfig = Object.freeze({
  mode: "fixed",
  fixedFrameCount: 4,
  frameIntervalSec: 1,
  minFrameCount: 1,
  maxFrameCount: 60,
  twoStage: !0,
  temperature: 0.22,
  jointMotionBoost: 1.35,
  eyeOpenBinary: !0,
  minVisibleRatio: 0.08,
  maxPromptParameters: 96
});

// node_modules/@soullink-emotion/profile-generator/dist/index.js
var STANDARD_PARAM_TABLE = {
  eyeOpen: {
    group: "EyeBlink",
    pair: { left: ["ParamEyeLOpen"], right: ["ParamEyeROpen"] },
    mode: "set",
    scale: 1,
    min: 0,
    max: 1.2
  },
  eyeSmile: {
    pair: { left: ["ParamEyeLSmile"], right: ["ParamEyeRSmile"] },
    mode: "set",
    scale: 1,
    min: 0,
    max: 1
  },
  gazeX: { ids: ["ParamEyeBallX"], mode: "set", scale: 1, min: -1, max: 1 },
  gazeY: { ids: ["ParamEyeBallY"], mode: "set", scale: 1, min: -1, max: 1 },
  headX: { ids: ["ParamAngleX"], mode: "set", scale: 30, min: -30, max: 30 },
  headY: { ids: ["ParamAngleY"], mode: "set", scale: 30, min: -30, max: 30 },
  headZ: { ids: ["ParamAngleZ"], mode: "set", scale: 30, min: -30, max: 30 },
  bodyX: { ids: ["ParamBodyAngleX"], mode: "set", scale: 12, min: -12, max: 12 },
  bodyY: { ids: ["ParamBodyAngleY"], mode: "set", scale: 12, min: -12, max: 12 },
  bodyZ: { ids: ["ParamBodyAngleZ"], mode: "set", scale: 12, min: -12, max: 12 },
  mouthSmile: { ids: ["ParamMouthForm"], mode: "set", scale: 1, min: -1, max: 1 },
  mouthOpen: {
    group: "LipSync",
    ids: ["ParamMouthOpenY"],
    mode: "set",
    scale: 1,
    min: 0,
    max: 1
  },
  browInnerUp: {
    pair: { left: ["ParamBrowLY"], right: ["ParamBrowRY"] },
    mode: "set",
    scale: 1,
    min: -1,
    max: 1
  },
  browOuterUp: {
    pair: { left: ["ParamBrowLAngle"], right: ["ParamBrowRAngle"] },
    mode: "set",
    scale: 0.9,
    min: -1,
    max: 1
  },
  browDown: {
    pair: { left: ["ParamBrowLForm"], right: ["ParamBrowRForm"] },
    mode: "set",
    scale: -0.85,
    min: -1,
    max: 1
  },
  blush: { ids: ["ParamCheek"], mode: "set", scale: 1, min: 0, max: 1 },
  breath: { ids: ["ParamBreath"], mode: "set", scale: 1, min: 0, max: 1 }
};
function resolveStandard(key, params, groups) {
  let spec = STANDARD_PARAM_TABLE[key];
  if (!spec) return;
  let paramIds = new Set(params.map((param) => param.id));
  if (spec.group) {
    let groupIds = (groups.find(
      (candidate) => candidate.Target === "Parameter" && candidate.Name === spec.group
    )?.Ids ?? []).filter((id) => paramIds.has(id));
    if (groupIds.length > 0)
      return { ids: groupIds, source: "standard-group" };
  }
  let candidateIds = spec.pair ? [...spec.pair.left, ...spec.pair.right] : spec.ids ?? [];
  if (candidateIds.length > 0 && candidateIds.every((id) => paramIds.has(id)))
    return { ids: [...candidateIds], source: "standard-id" };
}
var profileGeneratorVersion = "soullink-profile-autogen-v3", Live2DProfileAutoGenerator = class {
  client;
  modelsRoot;
  modelsBaseUrl;
  useConfiguredOpenAI;
  defaultModelDir;
  constructor(options) {
    if (!options?.modelsRoot?.trim())
      throw new Error("Live2DProfileAutoGenerator requires a modelsRoot directory");
    this.client = options.client ?? new OpenAICompatibleClient(), this.modelsRoot = import_path.default.resolve(options.modelsRoot), this.modelsBaseUrl = normalizeModelsBaseUrl(options.modelsBaseUrl ?? "/models"), this.useConfiguredOpenAI = options.useConfiguredOpenAI ?? !1, this.defaultModelDir = sanitizeModelDir(options.defaultModelDir ?? "lilyabee");
  }
  async ensure(request) {
    let context = await this.loadContext(request.modelDir ?? this.defaultModelDir), existing = await this.readExistingProfile(context.profilePath), existingHash = existing?.sourceSignature?.hash, generatorRevisionCurrent = !existing?.autoProfile || existing.autoProfile.provider === "manual" || existing.autoProfile.promptVersion === profileGeneratorVersion, reason = request.force ? "forced" : existing ? existingHash === context.signature.hash && existing.modelPath === context.webModelPath && generatorRevisionCurrent ? "current" : "stale" : "missing";
    if (reason === "current" && existing)
      return {
        generated: !1,
        reason,
        provider: "existing",
        profileUrl: context.webProfilePath,
        modelUrl: context.webModelPath,
        sourceSignature: context.signature,
        profile: existing,
        notes: ["source signature is current"],
        // Provenance is unknown for a pre-existing profile; coverage infers
        // per-key confidence from whether targets are standard Cubism ids.
        coverage: computeAdaptationCoverage(existing, context.parameters, {
          modelDir: context.modelDir,
          provider: "existing",
          provenance: void 0
        })
      };
    let provenance = {}, heuristic = await this.createHeuristicProfile(context, request.displayName ?? existing?.displayName, provenance), notes = [`generation reason: ${reason}`], provider = "heuristic", profile = heuristic;
    if (shouldUseLLM(request.openAI, this.useConfiguredOpenAI) && this.client.isConfigured(request.openAI))
      try {
        let llmProfile = await this.generateWithLLM(context, heuristic, existing, request.openAI);
        profile = await this.sanitizeProfile(llmProfile, heuristic, context, "openai-compatible"), provider = "openai-compatible", notes.push("LLM profile accepted after parameter validation");
      } catch (error) {
        notes.push(`LLM profile generation fell back to heuristic: ${error instanceof Error ? error.message : String(error)}`);
      }
    else
      notes.push("Explicit OpenAI-compatible settings were not provided; used heuristic scanner");
    return provider === "heuristic" && (profile = await this.sanitizeProfile(profile, heuristic, context, "heuristic")), await this.writeProfile(context.profilePath, profile), {
      generated: !0,
      reason,
      provider,
      profileUrl: context.webProfilePath,
      modelUrl: context.webModelPath,
      sourceSignature: context.signature,
      profile,
      notes,
      coverage: computeAdaptationCoverage(profile, context.parameters, {
        modelDir: context.modelDir,
        provider,
        provenance
      })
    };
  }
  /**
   * Persist a manually calibrated profile. Overlays only the sanitized incoming
   * rules onto the existing profile, preserves the existing source signature
   * (never rehashes), recomputes neutralParams/parameterSmoothing/capabilities,
   * and marks the profile as provider="manual".
   */
  async saveCalibratedProfile(request) {
    let context = await this.loadContext(request.modelDir), parameterIds = new Set(context.parameters.map((parameter) => parameter.id)), mouthOpenParameterIds = new Set(
      context.parameters.filter(isMouthOpenLive2DParameter).map((parameter) => parameter.id)
    ), base = await this.readExistingProfile(context.profilePath) ?? await this.createHeuristicProfile(context, request.displayName), parameterMap = { ...base.parameterMap }, rawIncomingMap = request.parameterMap && typeof request.parameterMap == "object" && !Array.isArray(request.parameterMap) ? request.parameterMap : {};
    for (let key of facsKeys) {
      let rule = sanitizeRule(rawIncomingMap[key], parameterIds);
      rule && (parameterMap[key] = rule);
    }
    let customParams = { ...base.customParams ?? {} }, rawIncomingCustom = request.customParams && typeof request.customParams == "object" && !Array.isArray(request.customParams) ? request.customParams : {};
    for (let [key, value] of Object.entries(rawIncomingCustom)) {
      let rule = sanitizeRule(value, parameterIds);
      rule && (customParams[key] = rule);
    }
    let hasCustomParams = Object.keys(customParams).length > 0, privateEmotionMap = sanitizePrivateEmotionMap(
      request.privateEmotionMap,
      parameterIds,
      base.privateEmotionMap ?? {},
      "manual",
      mouthOpenParameterIds
    ), hasPrivateEmotionMap = Object.keys(privateEmotionMap).length > 0, derivedBase = { parameterMap, ...hasCustomParams ? { customParams } : {} }, preservedSignature = base.sourceSignature ?? context.signature, resultSignature = {
      modelDir: preservedSignature.modelDir ?? context.signature.modelDir,
      model3File: preservedSignature.model3File ?? context.signature.model3File,
      cdi3File: preservedSignature.cdi3File ?? context.signature.cdi3File,
      hash: preservedSignature.hash,
      generatedAt: preservedSignature.generatedAt ?? context.signature.generatedAt
    }, profile = {
      modelId: base.modelId,
      displayName: request.displayName?.trim() || base.displayName,
      version: base.version,
      modelPath: context.webModelPath,
      sourceSignature: preservedSignature,
      autoProfile: {
        provider: "manual",
        promptVersion: base.autoProfile?.promptVersion ?? profileGeneratorVersion,
        generatedAt: (/* @__PURE__ */ new Date()).toISOString(),
        notes: ["Manually calibrated profile saved via /profile/save."]
      },
      schemaVersion: CURRENT_SCHEMA_VERSION,
      capabilities: emptyCapabilities(),
      parameterMap,
      ...hasCustomParams ? { customParams } : {},
      ...hasPrivateEmotionMap ? { privateEmotionMap } : {},
      idleConfig: this.sanitizeIdleConfig(request.idleConfig, base.idleConfig, parameterMap),
      reactionBias: base.reactionBias,
      neutralParams: {
        ...deriveNeutralParams(derivedBase),
        ...sanitizeNumericRecord(request.neutralParams, parameterIds)
      },
      parameterSmoothing: deriveParameterSmoothing(derivedBase)
    };
    return profile.capabilities = detectCapabilities(profile), await this.writeProfile(context.profilePath, profile), {
      generated: !0,
      reason: "forced",
      provider: "manual",
      profileUrl: context.webProfilePath,
      modelUrl: context.webModelPath,
      sourceSignature: resultSignature,
      profile,
      notes: [
        "Manual calibration saved.",
        "Existing source signature preserved (not rehashed)."
      ],
      // Provenance is unknown for a manual save; coverage infers per-key
      // confidence from whether targets are standard Cubism ids.
      coverage: computeAdaptationCoverage(profile, context.parameters, {
        modelDir: context.modelDir,
        provider: "manual",
        provenance: void 0
      })
    };
  }
  async loadContext(modelDirInput) {
    let modelDir = sanitizeModelDir(modelDirInput), directoryPath = import_path.default.resolve(this.modelsRoot, modelDir);
    if (!isInside(this.modelsRoot, directoryPath))
      throw new Error("modelDir must stay inside the configured models root");
    let entries = await import_fs.promises.readdir(directoryPath), model3File = entries.find((entry) => entry.toLowerCase().endsWith(".model3.json"));
    if (!model3File) throw new Error(`No .model3.json file found in ${modelDir}`);
    let model3Path = import_path.default.join(directoryPath, model3File), model3 = await readJson(model3Path), displayInfo = model3.FileReferences?.DisplayInfo, cdi3File = typeof displayInfo == "string" && displayInfo.trim() ? normalizeRelativeFile(displayInfo) : entries.find((entry) => entry.toLowerCase().endsWith(".cdi3.json")), cdi3Path = cdi3File ? resolveModelFile(directoryPath, cdi3File) : void 0, cdi3 = cdi3Path ? await readOptionalJson(cdi3Path) : void 0, profilePath = import_path.default.join(directoryPath, "soullink.profile.json"), groups = Array.isArray(model3.Groups) ? model3.Groups : [], expressions = model3.FileReferences?.Expressions?.map((expression) => ({
      name: String(expression.Name ?? ""),
      file: String(expression.File ?? "")
    })).filter((expression) => expression.name || expression.file) ?? [], motionGroups = Object.entries(model3.FileReferences?.Motions ?? {}).map(([group, motions]) => ({
      group,
      files: Array.isArray(motions) ? motions.map((motion) => String(motion.File ?? "")) : []
    })).filter((motionGroup) => motionGroup.group || motionGroup.files.some(Boolean)), parameters = buildParameterInfo(cdi3), signature = await this.createSignature({
      modelDir,
      directoryPath,
      model3File,
      model3Path,
      cdi3File,
      cdi3Path,
      model3
    });
    return {
      modelDir,
      directoryPath,
      model3File,
      model3Path,
      cdi3File,
      cdi3Path,
      profilePath,
      webModelPath: joinModelsUrl(this.modelsBaseUrl, modelDir, toWebPath(model3File)),
      webProfilePath: joinModelsUrl(this.modelsBaseUrl, modelDir, "soullink.profile.json"),
      model3,
      cdi3,
      parameters,
      groups,
      expressions,
      expressionFiles: expressions,
      motionGroups,
      signature
    };
  }
  async createSignature(input) {
    let hash = (0, import_crypto.createHash)("sha256");
    hash.update(`modelDir:${input.modelDir}
`), hash.update(`model3File:${input.model3File}
`), hash.update(await import_fs.promises.readFile(input.model3Path)), input.cdi3Path && (hash.update(`
cdi3File:${input.cdi3File ?? ""}
`), hash.update(await import_fs.promises.readFile(input.cdi3Path)));
    let moc = input.model3.FileReferences?.Moc;
    if (moc) {
      let mocPath = resolveModelFile(input.directoryPath, moc), stat = await statOptional(mocPath);
      stat && hash.update(`
moc:${moc}:${stat.size}:${Math.round(stat.mtimeMs)}`);
    }
    for (let expression of input.model3.FileReferences?.Expressions ?? [])
      if (!(!expression.File || !expression.File.toLowerCase().endsWith(".exp3.json")))
        try {
          let expressionPath = resolveModelFile(input.directoryPath, expression.File), content = await readOptionalFile(expressionPath);
          content && hash.update(`
expression:${expression.Name ?? ""}:${expression.File}
`).update(content);
        } catch {
        }
    for (let [group, entries] of Object.entries(input.model3.FileReferences?.Motions ?? {}))
      for (let i = 0; i < entries.length; i++) {
        let file = entries[i]?.File;
        if (!(!file || !file.toLowerCase().endsWith(".motion3.json")))
          try {
            let motionPath = resolveModelFile(input.directoryPath, file), stat = await statOptional(motionPath);
            stat && hash.update(`
motion:${group}:${i}:${file}:${stat.size}:${Math.round(stat.mtimeMs)}`);
          } catch {
          }
      }
    return {
      modelDir: input.modelDir,
      model3File: input.model3File,
      cdi3File: input.cdi3File,
      hash: hash.digest("hex"),
      generatedAt: (/* @__PURE__ */ new Date()).toISOString()
    };
  }
  async createHeuristicProfile(context, displayName, provenance = {}) {
    let selector = new ParameterSelector(context.parameters), params = context.parameters, groups = context.groups, paramIdSet = new Set(params.map((param) => param.id)), map = {}, addRule = (key, rule) => {
      rule && (map[key] = rule);
    }, resolveSingle = (key, nameMatch) => {
      let std = resolveStandard(key, params, groups);
      if (std && std.ids.length)
        return provenance[key] = std.source, std.ids[0];
      if (nameMatch)
        return provenance[key] = "name-match", nameMatch;
    }, resolveMulti = (key, nameMatch) => {
      let std = resolveStandard(key, params, groups);
      return std && std.ids.length ? (provenance[key] = std.source, std.ids) : nameMatch.length ? (provenance[key] = "name-match", nameMatch) : [];
    }, eyeOpen = selector.eyeOpenPair(context.groups), eyeBlinkGroupName = STANDARD_PARAM_TABLE.eyeOpen?.group, blinkGroupIds = (groups.find((group) => group.Target === "Parameter" && group.Name === eyeBlinkGroupName)?.Ids ?? []).filter((id) => paramIdSet.has(id)), eyeSmile = resolveMulti("eyeSmile", selector.pair(["eyesmile", "eye smile", "\u5FAE\u7B11"], ["ParamEyeLSmile"], ["ParamEyeRSmile"])), gazeX = resolveSingle("gazeX", selector.one(["eyeballx", "eye x", "\u773C\u73E0x", "\u773C\u7403x"], ["ParamEyeBallX"])), gazeY = resolveSingle("gazeY", selector.one(["eyebally", "eye y", "\u773C\u73E0y", "\u773C\u7403y"], ["ParamEyeBallY"])), headX = resolveSingle("headX", selector.one(["anglex", "\u89D2\u5EA6x"], ["ParamAngleX"])), headY = resolveSingle("headY", selector.one(["angley", "\u89D2\u5EA6y"], ["ParamAngleY"])), headZ = resolveSingle("headZ", selector.one(["anglez", "\u89D2\u5EA6z"], ["ParamAngleZ"])), bodyX = resolveSingle("bodyX", selector.one(["bodyanglex", "\u8EAB\u4F53\u65CB\u8F6Cx", "\u8EAB\u4F53x"], ["ParamBodyAngleX"])), bodyY = resolveSingle("bodyY", selector.one(["bodyangley", "\u8EAB\u4F53\u65CB\u8F6Cy", "\u8EAB\u4F53y"], ["ParamBodyAngleY"])), bodyZ = resolveSingle("bodyZ", selector.one(["bodyanglez", "\u8EAB\u4F53\u65CB\u8F6Cz", "\u8EAB\u4F53z"], ["ParamBodyAngleZ"])), mouthForm = resolveSingle("mouthSmile", selector.one(["mouthform", "\u5634\u53D8\u5F62", "\u5634\u3000\u53D8\u5F62"], ["ParamMouthForm"])), mouthOpen = resolveSingle("mouthOpen", selector.one(["mouthopeny", "\u5634\u5F20\u5F00", "\u5F20\u5F00\u548C\u95ED\u5408"], ["ParamMouthOpenY"])), mouthPucker = selector.one(["mouthpucker", "\u9F13\u5634", "\u561F\u5634"], []), browY = resolveMulti("browInnerUp", selector.pair(["brow", "\u7709", "\u4E0A\u4E0B"], ["ParamBrowLY"], ["ParamBrowRY"])), browAngle = resolveMulti("browOuterUp", selector.pair(["brow", "\u7709", "angle", "\u89D2\u5EA6"], ["ParamBrowLAngle"], ["ParamBrowRAngle"])), browForm = resolveMulti("browDown", selector.pair(["brow", "\u7709", "form", "\u5909\u5F62", "\u53D8\u5F62"], ["ParamBrowLForm"], ["ParamBrowRForm"])), blushName = selector.many(["blush", "\u8138\u7EA2", "\u8138\u988A\u6CDB\u7EA2", "\u816E\u7EA2"], ["\u8138\u9ED1"]), stdBlush = resolveStandard("blush", params, groups), blush;
    stdBlush && stdBlush.ids.length ? (blush = unique([...blushName, ...stdBlush.ids]), provenance.blush = stdBlush.source) : blushName.length ? (blush = blushName, provenance.blush = "name-match") : blush = [];
    let tear = selector.many(["tear", "\u6CEA", "\u773C\u6CEA"], []);
    tear.length && (provenance.tear = "name-match");
    let sweat = selector.many(["sweat", "\u6C57"], []);
    sweat.length && (provenance.sweat = "name-match");
    let breath = resolveSingle("breath", selector.one(["breath", "\u547C\u5438"], ["ParamBreath"]));
    mouthPucker && (provenance.mouthPucker = "name-match"), eyeOpen.length && (provenance.eyeOpen = blinkGroupIds.length >= 2 ? "standard-group" : eyeOpen.every((id) => isStandardId(id)) ? "standard-id" : "name-match", addRule("eyeOpen", { targets: eyeOpen, mode: "set", scale: 1, min: 0, max: 1.2 }), eyeOpen[0] && (addRule("eyeBlinkL", { target: eyeOpen[0], mode: "subtract", scale: 1, min: 0, max: 1.2 }), provenance.eyeBlinkL = "derived"), eyeOpen[1] && (addRule("eyeBlinkR", { target: eyeOpen[1], mode: "subtract", scale: 1, min: 0, max: 1.2 }), provenance.eyeBlinkR = "derived"), addRule("eyeSquint", { targets: eyeOpen, mode: "subtract", scale: 0.22, min: 0, max: 1.2 }), provenance.eyeSquint = "derived"), addRule("eyeSmile", ruleForTargets(eyeSmile, "set", 1, 0, 1)), addRule("gazeX", ruleForTarget(gazeX, "set", 1, -1, 1)), addRule("gazeY", ruleForTarget(gazeY, "set", 1, -1, 1)), addRule("headX", ruleForTarget(headX, "set", 30, -30, 30)), addRule("headY", ruleForTarget(headY, "set", 30, -30, 30)), addRule("headZ", ruleForTarget(headZ, "set", 30, -30, 30)), addRule("bodyX", ruleForTarget(bodyX, "set", 12, -12, 12)), addRule("bodyY", ruleForTarget(bodyY, "set", 12, -12, 12)), addRule("bodyZ", ruleForTarget(bodyZ, "set", 12, -12, 12)), addRule("mouthSmile", ruleForTarget(mouthForm, "set", 1, -1, 1)), addRule("mouthFrown", ruleForTarget(mouthForm, "subtract", 1, -1, 1)), mouthForm && (provenance.mouthFrown = "derived"), addRule("mouthOpen", ruleForTarget(mouthOpen, "set", 1, 0, 1)), addRule("mouthPucker", ruleForTarget(mouthPucker, "set", 1, 0, 1)), addRule("browInnerUp", ruleForTargets(browY, "set", 1, -1, 1)), addRule("browOuterUp", ruleForTargets(browAngle, "set", 0.9, -1, 1)), addRule("browDown", ruleForTargets(browForm, "set", -0.85, -1, 1)), addRule("blush", ruleForTargets(blush, "set", 1, 0, 1)), addRule("tear", ruleForTargets(tear, "set", 1, 0, 1)), addRule("sweat", ruleForTargets(sweat, "set", 1, 0, 1)), addRule("breath", ruleForTarget(breath, "set", 1, 0, 1));
    let privateEmotionMap = buildHeuristicPrivateEmotionMap(params, mappedTargetIds(map)), catalogNotes = [], nativeCatalog = await this.buildNativeAnimationCatalog(context, catalogNotes), expressionMap = this.buildExpressionMap(context, nativeCatalog), nativeAnimationEntries = (nativeCatalog.expressions?.length ?? 0) + (nativeCatalog.motions?.length ?? 0), profile = {
      modelId: `${sanitizeId(context.modelDir)}_${context.signature.hash.slice(0, 8)}`,
      displayName: displayName?.trim() || context.modelDir,
      version: "1.0.0",
      modelPath: context.webModelPath,
      sourceSignature: context.signature,
      autoProfile: {
        provider: "heuristic",
        promptVersion: profileGeneratorVersion,
        generatedAt: context.signature.generatedAt,
        notes: [
          "Generated from model3/cdi3 parameter names.",
          "LLM may refine this profile when OpenAI-compatible settings are enabled.",
          ...catalogNotes
        ]
      },
      schemaVersion: CURRENT_SCHEMA_VERSION,
      capabilities: emptyCapabilities(),
      parameterMap: map,
      ...Object.keys(privateEmotionMap).length ? { privateEmotionMap } : {},
      idleConfig: this.createIdleConfig(map),
      reactionBias: {
        shy: {
          blushMultiplier: 1.1,
          gazeAwayMultiplier: 1.05
        },
        happy: {
          mouthSmileMultiplier: 1,
          eyeSmileMultiplier: 1
        }
      },
      neutralParams: deriveNeutralParams({ parameterMap: map }),
      parameterSmoothing: deriveParameterSmoothing({ parameterMap: map }),
      ...nativeAnimationEntries > 0 ? { nativeAnimations: nativeCatalog } : {},
      ...expressionMap ? { expressionMap } : {}
    };
    return profile.capabilities = detectCapabilities(profile), profile;
  }
  async generateWithLLM(context, heuristic, existing, openAI) {
    let lastError;
    for (let responseFormat of responseFormatFallbacks(profileResponseFormat))
      try {
        let completion = await this.client.createChatCompletion({
          model: openAI?.model,
          messages: [
            {
              role: "system",
              content: buildProfileSystemPrompt()
            },
            {
              role: "user",
              content: JSON.stringify({
                task: "Generate soullink.profile.json for this Live2D model.",
                sourceSignature: context.signature,
                modelPathMustEqual: context.webModelPath,
                cdiParameters: context.parameters,
                model3Groups: context.groups,
                expressions: context.expressions,
                heuristicDraft: heuristic,
                existingProfileReference: existing ?? null,
                canonicalReference: canonicalProfileReference()
              })
            }
          ],
          temperature: 0.18,
          max_tokens: 4500,
          ...responseFormat ? { response_format: responseFormat } : {}
        }, openAI);
        return parseJSON(completion.choices[0]?.message?.content ?? "");
      } catch (error) {
        if (lastError = error, error instanceof OpenAIClientNotConfiguredError) throw error;
      }
    throw lastError instanceof Error ? lastError : new Error(String(lastError));
  }
  async sanitizeProfile(raw, heuristic, context, provider) {
    let parameterIds = new Set(context.parameters.map((parameter) => parameter.id)), profile = raw, rawMap = profile.parameterMap && typeof profile.parameterMap == "object" ? profile.parameterMap : {}, parameterMap = { ...heuristic.parameterMap };
    for (let key of facsKeys) {
      if (provider === "openai-compatible" && Object.prototype.hasOwnProperty.call(rawMap, key) && rawMap[key] === null) {
        delete parameterMap[key];
        continue;
      }
      let rule = sanitizeRule(rawMap[key], parameterIds);
      rule && (parameterMap[key] = rule);
    }
    let rawCustomParams = profile.customParams && typeof profile.customParams == "object" && !Array.isArray(profile.customParams) ? profile.customParams : {}, customParams = {};
    for (let [key, value] of Object.entries(rawCustomParams)) {
      let rule = sanitizeRule(value, parameterIds);
      rule && (customParams[key] = rule);
    }
    let hasCustomParams = Object.keys(customParams).length > 0, privateEmotionMap = sanitizePrivateEmotionMap(
      profile.privateEmotionMap,
      parameterIds,
      heuristic.privateEmotionMap ?? {},
      provider === "openai-compatible" ? "llm" : "heuristic",
      new Set(context.parameters.filter(isMouthOpenLive2DParameter).map((parameter) => parameter.id))
    ), hasPrivateEmotionMap = Object.keys(privateEmotionMap).length > 0, derivedBase = { parameterMap, ...hasCustomParams ? { customParams } : {} }, catalogNotes = [], nativeCatalog = await this.buildNativeAnimationCatalog(context, catalogNotes), nativeAnimationEntries = (nativeCatalog.expressions?.length ?? 0) + (nativeCatalog.motions?.length ?? 0), catalogExpressionNames = new Set((nativeCatalog.expressions ?? []).map((e) => e.name)), rawExpressionMap = profile.expressionMap && typeof profile.expressionMap == "object" && !Array.isArray(profile.expressionMap) ? profile.expressionMap : {}, expressionMap = {};
    for (let [key, value] of Object.entries(rawExpressionMap))
      if (typeof key == "string") {
        if (typeof value == "string")
          catalogExpressionNames.has(value) && (expressionMap[key] = value);
        else if (value && typeof value == "object" && !Array.isArray(value)) {
          let record = value, exprName = typeof record.expression == "string" ? record.expression : void 0;
          if (exprName && catalogExpressionNames.has(exprName)) {
            let minIntensity = typeof record.minIntensity == "number" && Number.isFinite(record.minIntensity) && record.minIntensity >= 0 && record.minIntensity <= 1 ? record.minIntensity : void 0;
            expressionMap[key] = {
              expression: exprName,
              ...minIntensity !== void 0 ? { minIntensity } : {}
            };
          }
        }
      }
    let hasExpressionMap = Object.keys(expressionMap).length > 0, rawMotionMap = profile.motionMap && typeof profile.motionMap == "object" && !Array.isArray(profile.motionMap) ? profile.motionMap : {}, motionMap = {}, catalogMotions = nativeCatalog.motions ?? [];
    for (let [key, value] of Object.entries(rawMotionMap)) {
      if (!value || typeof value != "object" || Array.isArray(value)) continue;
      let record = value, group = typeof record.group == "string" && record.group.trim() ? record.group.trim() : void 0, index = typeof record.index == "number" && Number.isInteger(record.index) && record.index >= 0 ? record.index : void 0;
      if (!group || !catalogMotions.some((motion) => motion.group === group && (index === void 0 || motion.index === index))) continue;
      let priority = isMotionPriority(record.priority) ? record.priority : void 0;
      motionMap[key] = {
        group,
        ...index !== void 0 ? { index } : {},
        ...priority ? { priority } : {}
      };
    }
    let hasMotionMap = Object.keys(motionMap).length > 0, result = {
      modelId: stringOr(profile.modelId, heuristic.modelId),
      displayName: stringOr(profile.displayName, heuristic.displayName),
      version: stringOr(profile.version, heuristic.version),
      modelPath: context.webModelPath,
      sourceSignature: context.signature,
      autoProfile: {
        provider,
        promptVersion: profileGeneratorVersion,
        generatedAt: context.signature.generatedAt,
        notes: [
          ...provider === "openai-compatible" ? ["Generated with LLM and validated against actual CDI parameters."] : heuristic.autoProfile?.notes ?? [],
          ...catalogNotes
        ]
      },
      schemaVersion: CURRENT_SCHEMA_VERSION,
      capabilities: emptyCapabilities(),
      parameterMap,
      ...hasCustomParams ? { customParams } : {},
      ...hasPrivateEmotionMap ? { privateEmotionMap } : {},
      idleConfig: this.sanitizeIdleConfig(profile.idleConfig, heuristic.idleConfig, parameterMap),
      reactionBias: profile.reactionBias && typeof profile.reactionBias == "object" ? profile.reactionBias : heuristic.reactionBias,
      neutralParams: {
        ...deriveNeutralParams(derivedBase),
        ...sanitizeNumericRecord(profile.neutralParams, parameterIds)
      },
      parameterSmoothing: {
        ...deriveParameterSmoothing(derivedBase),
        ...sanitizeNumericRecord(profile.parameterSmoothing, parameterIds)
      },
      ...nativeAnimationEntries > 0 ? { nativeAnimations: nativeCatalog } : {},
      ...hasExpressionMap ? { expressionMap } : {},
      ...hasMotionMap ? { motionMap } : {}
    };
    return result.capabilities = detectCapabilities(result), result;
  }
  /**
   * C5-T4/C5-T6: Scan expression (.exp3.json) and motion (.motion3.json) files
   * from the model directory and build the NativeAnimationCatalog. All file paths
   * are validated with isInside before access. Files > 256 KB, paths outside the
   * model directory, or unexpected extensions are skipped with a note in the
   * provided notes array.
   */
  async buildNativeAnimationCatalog(context, notes = []) {
    let expressions = [];
    for (let { name, file } of context.expressionFiles) {
      if (expressions.length >= 64) {
        notes.push("Expression limit (64) reached; skipping remaining expression files.");
        break;
      }
      if (!(!file || !file.toLowerCase().endsWith(".exp3.json")))
        try {
          let resolved = import_path.default.resolve(context.directoryPath, normalizeRelativeFile(file));
          if (!isInside(context.directoryPath, resolved)) {
            notes.push(`Expression file "${file}" escapes model directory; skipped.`);
            continue;
          }
          let stat = await statOptional(resolved);
          if (!stat) continue;
          if (stat.size > 262144) {
            notes.push(`Expression file "${file}" skipped (${stat.size} bytes exceeds 256 KB limit).`);
            continue;
          }
          let raw = await readOptionalFile(resolved);
          if (!raw) continue;
          let exp3 = JSON.parse(raw.toString("utf8")), params = [];
          for (let param of exp3.Parameters ?? [])
            typeof param.Id == "string" && param.Id && typeof param.Value == "number" && param.Value !== 0 && params.push(param.Id);
          expressions.push({ name, file, ...params.length ? { params } : {} });
        } catch {
          notes.push(`Failed to process expression file "${file}"; skipped.`);
        }
    }
    let motions = [], motionsRecord = context.model3.FileReferences?.Motions ?? {};
    outer: for (let [group, entries] of Object.entries(motionsRecord))
      if (Array.isArray(entries))
        for (let index = 0; index < entries.length; index++) {
          if (motions.length >= 256) {
            notes.push("Motion limit (256) reached; skipping remaining motion entries.");
            break outer;
          }
          let file = entries[index]?.File;
          if (!(!file || typeof file != "string") && file.toLowerCase().endsWith(".motion3.json"))
            try {
              let resolved = resolveModelFile(context.directoryPath, file);
              if (!isInside(context.directoryPath, resolved)) {
                notes.push(`Motion file "${file}" (group "${group}", index ${index}) escapes model directory; skipped.`);
                continue;
              }
              motions.push({ group, index, file });
            } catch {
              notes.push(`Motion file "${file}" (group "${group}", index ${index}) failed path check; skipped.`);
            }
        }
    return {
      ...expressions.length ? { expressions } : {},
      ...motions.length ? { motions } : {}
    };
  }
  /**
   * C5-T4: Heuristic name->emotion mapping. Returns a Record keyed by emotion
   * name whose values are the best-matching expression name from the catalog.
   * Returns undefined when no expression maps to any known emotion.
   */
  buildExpressionMap(_context, catalog) {
    let expressionList = catalog.expressions ?? [];
    if (!expressionList.length) return;
    let emotionHeuristics = [
      [["blush", "\u8138\u7EA2", "embarrassed"], "shy"],
      [["angry", "\u6012", "anger"], "angry"],
      [["tears", "\u6CEA", "tear", "cry", "sad", "\u60B2"], "sad"],
      [["loveeyes", "love", "\u7231", "heart"], "affectionate"],
      [["stars", "excited", "star", "\u5174\u594B"], "excited"],
      [["confused", "\u5E7D\u7075", "ghost"], "confused"],
      [["smile", "happy", "\u5F00\u5FC3"], "happy"],
      [["surprised", "\u60CA", "wow"], "surprised"]
    ], sorted = [...expressionList].sort((a, b) => a.file.localeCompare(b.file)), result = {}, emotionClaimed = /* @__PURE__ */ new Set();
    for (let { name } of sorted) {
      let normalized = normalizeText(name);
      for (let [needles, emotion] of emotionHeuristics)
        if (!emotionClaimed.has(emotion) && needles.some((needle) => normalized.includes(normalizeText(needle)))) {
          result[emotion] = name, emotionClaimed.add(emotion);
          break;
        }
    }
    return Object.keys(result).length ? result : void 0;
  }
  createIdleConfig(map) {
    let idleConfig = {};
    return map.gazeX && (idleConfig.gazeX = [-0.12, 0.12]), map.gazeY && (idleConfig.gazeY = [-0.06, 0.08]), map.headX && (idleConfig.headX = [-0.08, 0.08]), map.headY && (idleConfig.headY = [-0.04, 0.04]), map.headZ && (idleConfig.headZ = [-0.05, 0.05]), map.bodyX && (idleConfig.bodyX = [-0.045, 0.045]), map.bodyY && (idleConfig.bodyY = [-0.014, 0.014]), map.bodyZ && (idleConfig.bodyZ = [-0.055, 0.055]), map.mouthSmile && (idleConfig.mouthSmile = [0.02, 0.1]), map.browInnerUp && (idleConfig.browInnerUp = [0, 0.06]), map.eyeOpen && (idleConfig.eyeOpen = [0.9, 1]), idleConfig;
  }
  sanitizeIdleConfig(raw, fallback, map) {
    if (!raw || typeof raw != "object") return fallback;
    let record = raw, result = { ...fallback };
    for (let key of facsKeys) {
      if (!map[key]) continue;
      let value = record[key];
      if (!Array.isArray(value) || value.length !== 2) continue;
      let min = typeof value[0] == "number" && Number.isFinite(value[0]) ? value[0] : void 0, max = typeof value[1] == "number" && Number.isFinite(value[1]) ? value[1] : void 0;
      min === void 0 || max === void 0 || min > max || (result[key] = [min, max]);
    }
    return result;
  }
  async readExistingProfile(profilePath) {
    return await readOptionalJson(profilePath);
  }
  async writeProfile(profilePath, profile) {
    let temporaryPath = `${profilePath}.${process.pid}-${(0, import_crypto.randomBytes)(6).toString("hex")}.tmp`;
    try {
      await import_fs.promises.writeFile(temporaryPath, `${JSON.stringify(profile, null, 2)}
`, "utf8"), await import_fs.promises.rename(temporaryPath, profilePath);
    } finally {
      await import_fs.promises.rm(temporaryPath, { force: !0 });
    }
  }
}, ParameterSelector = class {
  constructor(parameters) {
    this.parameters = parameters;
    for (let parameter of parameters)
      this.byId.set(parameter.id, parameter);
  }
  parameters;
  byId = /* @__PURE__ */ new Map();
  eyeOpenPair(groups) {
    let ids = groups.find((group) => group.Target === "Parameter" && group.Name === "EyeBlink")?.Ids?.filter((id) => this.byId.has(id)) ?? [];
    return ids.length >= 2 ? ids.slice(0, 2) : this.pair(["eyeopen", "\u5F00\u95ED"], ["ParamEyeLOpen"], ["ParamEyeROpen"]);
  }
  pair(sharedNeedles, leftIds, rightIds) {
    let left = this.preferred(leftIds) ?? this.bestMatch([sharedNeedles, ["left", "\u5DE6", " l"]]), right = this.preferred(rightIds) ?? this.bestMatch([sharedNeedles, ["right", "\u53F3", " r"]]);
    return [left, right].filter((id) => !!id);
  }
  one(needles, preferredIds) {
    return this.preferred(preferredIds) ?? this.bestMatch([needles]);
  }
  many(needles, exclusions) {
    let normalizedExclusions = exclusions.map(normalizeText).filter(Boolean), result = [];
    for (let parameter of this.parameters) {
      let haystack = normalizeText(`${parameter.id} ${parameter.name} ${parameter.groupName}`);
      normalizedExclusions.some((needle) => haystack.includes(needle)) || needles.some((needle) => matchesSemanticNeedle(parameter, needle)) && result.push(parameter.id);
    }
    return unique(result).slice(0, 4);
  }
  preferred(ids) {
    return ids.find((id) => this.byId.has(id));
  }
  bestMatch(needleGroups) {
    let best;
    for (let parameter of this.parameters) {
      let groupScores = needleGroups.map((needles) => needles.filter((needle) => matchesSelectorNeedle(parameter, needle)).length);
      if (groupScores.some((score2) => score2 === 0)) continue;
      let score = groupScores.reduce((sum, value) => sum + value, 0);
      (!best || score > best.score) && (best = { id: parameter.id, score });
    }
    return best?.id;
  }
};
function mappedTargetIds(map) {
  let result = /* @__PURE__ */ new Set();
  for (let rule of Object.values(map)) {
    rule?.target && result.add(rule.target);
    for (let target of rule?.targets ?? []) result.add(target);
  }
  return result;
}
function buildHeuristicPrivateEmotionMap(parameters, excludedIds) {
  let definitions = [
    {
      key: "positiveEye",
      category: "positiveEye",
      needles: ["\u7231\u5FC3\u773C", "\u661F\u661F\u773C", "heart eye", "love eye", "star eye", "sparkle eye"],
      priority: 90,
      confidence: 0.94,
      exclusiveGroup: "face-effect"
    },
    {
      key: "confusionEffect",
      category: "privateEffect",
      needles: ["\u56F0\u60D1", "\u7591\u95EE", "confused", "confusion", "question mark"],
      emotions: ["confused"],
      priority: 95,
      confidence: 0.96,
      exclusiveGroup: "face-effect"
    },
    {
      key: "angerEffect",
      category: "anger",
      needles: ["\u751F\u6C14", "\u6124\u6012", "\u6012", "angry", "anger", "mad"],
      priority: 90,
      confidence: 0.95,
      exclusiveGroup: "face-effect"
    },
    {
      key: "shadowEffect",
      category: "shadow",
      needles: ["\u8138\u9ED1", "\u9ED1\u8138", "\u9634\u5F71", "shadow", "dark face"],
      priority: 80,
      confidence: 0.94,
      exclusiveGroup: "face-effect"
    },
    {
      key: "surpriseEffect",
      category: "surprise",
      needles: ["\u60CA\u8BB6", "\u9707\u60CA", "surprise", "shock"],
      priority: 80,
      confidence: 0.92,
      exclusiveGroup: "face-effect"
    },
    {
      key: "starEffect",
      category: "privateEffect",
      needles: ["\u661F\u661F", "star", "sparkle"],
      emotions: ["excited", "happy", "surprised"],
      priority: 70,
      confidence: 0.82,
      exclusiveGroup: "face-effect"
    }
  ], result = {}, claimed = /* @__PURE__ */ new Set();
  for (let definition of definitions) {
    let targets = parameters.filter((parameter) => !excludedIds.has(parameter.id) && !claimed.has(parameter.id)).filter((parameter) => definition.needles.some((needle) => matchesSemanticNeedle(parameter, needle))).map((parameter) => parameter.id).slice(0, 4);
    targets.length && (targets.forEach((target) => claimed.add(target)), result[definition.key] = {
      targets,
      category: definition.category,
      ...definition.emotions ? { emotions: definition.emotions } : {},
      ...definition.exclusiveGroup ? { exclusiveGroup: definition.exclusiveGroup } : {},
      priority: definition.priority,
      source: "heuristic",
      confidence: definition.confidence
    });
  }
  return result;
}
function buildProfileSystemPrompt() {
  return [
    "You are a Live2D Cubism parameter adapter engineer for SoullinkLive.",
    "Your job is to generate a maintainable soullink.profile.json that maps high-level FACS-like emotion keys to actual Live2D parameter IDs.",
    "",
    "Critical rules:",
    "1. Return JSON only. No markdown, no comments.",
    "2. Do not invent parameter IDs. Every target/targets entry must be selected from cdiParameters.id.",
    "3. Keep modelPath exactly equal to modelPathMustEqual.",
    "4. Prefer the heuristicDraft unless CDI parameter names clearly prove a better mapping.",
    "5. If a heuristic FACS mapping is clearly wrong, set that parameterMap key to null to delete it. Otherwise omit uncertain additions.",
    "6. Do not map cosmetic toggles, props, clothing, or hand poses to facial FACS unless their name clearly means the facial effect.",
    "7. Use stable Live2D conventions: eyeOpen is set to eye open params, eyeBlinkL/R subtract from each eye open param, mouthOpen uses mouth-open-y, mouthSmile/mouthFrown use mouth form when available.",
    "8. Directional keys gazeX/gazeY/headX/headY/headZ/bodyX/bodyY/bodyZ use signed ranges. Visual effect keys use 0..1 ranges.",
    "9. neutralParams should include every mapped target. Use eye open = 1, breath = 0.5, most others = 0 unless the reference says otherwise.",
    "10. parameterSmoothing should be modest: mouth/eyes fast, head/body medium, blush/tear/sweat slow.",
    "11. When adding model-specific controls outside the supported FACS keys, put them in customParams with validated target/targets entries.",
    "12. Use privateEmotionMap for semantic effect parameters that should react automatically to VAD/emotion, such as confused, anger symbols, stars, shadows, or surprise effects.",
    "13. privateEmotionMap must never target mouth-open/jaw-open parameters. Use emotions and/or vadRange for model-specific triggers, and an exclusiveGroup for mutually exclusive face effects.",
    "",
    `Supported FACS keys: ${facsKeys.join(", ")}.`,
    "ParameterMapRule format: { target?: string, targets?: string[], mode?: 'set'|'add'|'subtract'|'inverse', scale?: number, offset?: number, min?: number, max?: number, curve?: 'linear'|'easeIn'|'easeOut'|'easeInOut'|'smoothstep', gamma?: number, deadzone?: number, inputRange?: [number, number], outputRange?: [number, number], invertAround?: number }.",
    "PrivateEmotionMapping format: { target?: string, targets?: string[], category?: 'positiveEye'|'blush'|'tear'|'shadow'|'anger'|'sweat'|'surprise'|'privateEffect', emotions?: string[], vadRange?: { valence?: [number,number], arousal?: [number,number], dominance?: [number,number] }, triggerMode?: 'any'|'all', activeValue?: number, neutralValue?: number, intensity?: number, priority?: number, exclusiveGroup?: string, confidence?: number }.",
    "Output a complete ModelProfile object with schemaVersion, modelId, displayName, version, modelPath, capabilities, parameterMap, optional customParams/privateEmotionMap, idleConfig, neutralParams, parameterSmoothing, and optional reactionBias."
  ].join(`
`);
}
function canonicalProfileReference() {
  return {
    purpose: "Reference style based on the known LilyaBee adapter. Use as guidance, not as fixed parameter IDs for other models.",
    commonMappings: {
      eyeOpen: { targets: ["ParamEyeLOpen", "ParamEyeROpen"], mode: "set", scale: 1, min: 0, max: 1.2 },
      eyeBlinkL: { target: "ParamEyeLOpen", mode: "subtract", scale: 1, min: 0, max: 1.2 },
      eyeBlinkR: { target: "ParamEyeROpen", mode: "subtract", scale: 1, min: 0, max: 1.2 },
      gazeX: { target: "ParamEyeBallX", mode: "set", scale: 1, min: -1, max: 1 },
      gazeY: { target: "ParamEyeBallY", mode: "set", scale: 1, min: -1, max: 1 },
      headX: { target: "ParamAngleX", mode: "set", scale: 30, min: -30, max: 30 },
      headY: { target: "ParamAngleY", mode: "set", scale: 30, min: -30, max: 30 },
      headZ: { target: "ParamAngleZ", mode: "set", scale: 30, min: -30, max: 30 },
      bodyX: { target: "ParamBodyAngleX", mode: "set", scale: 12, min: -12, max: 12 },
      bodyY: { target: "ParamBodyAngleY", mode: "set", scale: 12, min: -12, max: 12 },
      bodyZ: { target: "ParamBodyAngleZ", mode: "set", scale: 12, min: -12, max: 12 },
      mouthSmile: { target: "ParamMouthForm", mode: "set", scale: 1, min: -1, max: 1 },
      mouthFrown: { target: "ParamMouthForm", mode: "subtract", scale: 1, min: -1, max: 1 },
      mouthOpen: { target: "ParamMouthOpenY", mode: "set", scale: 1, min: 0, max: 1 },
      blush: "Map only to params named blush/cheek/\u8138\u7EA2/\u8138\u988A\u6CDB\u7EA2.",
      tear: "Map only to params named tear/\u773C\u6CEA/\u6CEA.",
      sweat: "Map only to params named sweat/\u6C57."
    },
    privateEmotionExamples: {
      confusionEffect: {
        targets: ["ParamWithConfusedDisplayName"],
        category: "privateEffect",
        emotions: ["confused"],
        exclusiveGroup: "face-effect"
      }
    }
  };
}
function responseFormatFallbacks(schema) {
  return [
    schema,
    { type: "json_object" },
    void 0
  ];
}
function shouldUseLLM(openAI, useConfiguredOpenAI) {
  return openAI?.apiKey?.trim() ? !0 : useConfiguredOpenAI;
}
var mapRuleSchema = {
  type: "object",
  additionalProperties: !1,
  properties: {
    target: { type: "string" },
    targets: {
      type: "array",
      items: { type: "string" }
    },
    mode: {
      type: "string",
      enum: ["set", "add", "subtract", "inverse"]
    },
    scale: { type: "number" },
    offset: { type: "number" },
    min: { type: "number" },
    max: { type: "number" },
    curve: {
      type: "string",
      enum: ["linear", "easeIn", "easeOut", "easeInOut", "smoothstep"]
    },
    gamma: { type: "number" },
    deadzone: { type: "number" },
    inputRange: {
      type: "array",
      minItems: 2,
      maxItems: 2,
      items: { type: "number" }
    },
    outputRange: {
      type: "array",
      minItems: 2,
      maxItems: 2,
      items: { type: "number" }
    },
    invertAround: { type: "number" }
  }
}, parameterMapSchema = {
  type: "object",
  additionalProperties: !1,
  properties: Object.fromEntries(facsKeys.map((key) => [key, {
    oneOf: [mapRuleSchema, { type: "null" }]
  }]))
}, privateEmotionMappingSchema = {
  type: "object",
  additionalProperties: !1,
  properties: {
    target: { type: "string" },
    targets: { type: "array", items: { type: "string" } },
    category: {
      type: "string",
      enum: ["positiveEye", "blush", "tear", "shadow", "anger", "sweat", "surprise", "privateEffect"]
    },
    emotions: { type: "array", items: { type: "string" } },
    vadRange: {
      type: "object",
      additionalProperties: !1,
      properties: {
        valence: { type: "array", minItems: 2, maxItems: 2, items: { type: "number" } },
        arousal: { type: "array", minItems: 2, maxItems: 2, items: { type: "number" } },
        dominance: { type: "array", minItems: 2, maxItems: 2, items: { type: "number" } }
      }
    },
    triggerMode: { type: "string", enum: ["any", "all"] },
    activeValue: { type: "number" },
    neutralValue: { type: "number" },
    intensity: { type: "number", minimum: 0, maximum: 1 },
    priority: { type: "number" },
    exclusiveGroup: { type: "string" },
    source: { type: "string", enum: ["heuristic", "llm", "manual"] },
    confidence: { type: "number", minimum: 0, maximum: 1 }
  }
}, profileResponseFormat = {
  type: "json_schema",
  json_schema: {
    name: "soullink_live2d_profile",
    strict: !1,
    schema: {
      type: "object",
      additionalProperties: !0,
      required: ["modelId", "displayName", "version", "modelPath", "capabilities", "parameterMap", "idleConfig", "neutralParams", "parameterSmoothing"],
      properties: {
        modelId: { type: "string" },
        displayName: { type: "string" },
        version: { type: "string" },
        modelPath: { type: "string" },
        capabilities: {
          type: "object",
          additionalProperties: { type: "boolean" }
        },
        schemaVersion: { type: "number" },
        parameterMap: parameterMapSchema,
        customParams: {
          type: "object",
          additionalProperties: mapRuleSchema
        },
        privateEmotionMap: {
          type: "object",
          additionalProperties: {
            oneOf: [privateEmotionMappingSchema, { type: "null" }]
          }
        },
        idleConfig: {
          type: "object",
          additionalProperties: {
            type: "array",
            minItems: 2,
            maxItems: 2,
            items: { type: "number" }
          }
        },
        neutralParams: {
          type: "object",
          additionalProperties: { type: "number" }
        },
        parameterSmoothing: {
          type: "object",
          additionalProperties: { type: "number" }
        },
        reactionBias: {
          type: "object",
          additionalProperties: {
            type: "object",
            additionalProperties: { type: "number" }
          }
        },
        expressionMap: {
          type: "object",
          additionalProperties: {
            oneOf: [
              { type: "string" },
              {
                type: "object",
                properties: {
                  expression: { type: "string" },
                  minIntensity: { type: "number" }
                },
                required: ["expression"]
              }
            ]
          }
        },
        nativeAnimations: {
          type: "object",
          properties: {
            expressions: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  name: { type: "string" },
                  file: { type: "string" },
                  params: { type: "array", items: { type: "string" } }
                },
                required: ["name", "file"]
              }
            },
            motions: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  group: { type: "string" },
                  index: { type: "number" },
                  file: { type: "string" }
                },
                required: ["group", "index", "file"]
              }
            }
          }
        },
        motionMap: {
          type: "object",
          additionalProperties: {
            type: "object",
            properties: {
              group: { type: "string" },
              index: { type: "number" },
              priority: { type: "string", enum: ["idle", "normal", "force"] }
            },
            required: ["group"]
          }
        }
      }
    }
  }
};
async function readJson(filePath) {
  return JSON.parse(await import_fs.promises.readFile(filePath, "utf8"));
}
async function readOptionalJson(filePath) {
  try {
    return await readJson(filePath);
  } catch {
    return;
  }
}
async function readOptionalFile(filePath) {
  try {
    return await import_fs.promises.readFile(filePath);
  } catch {
    return;
  }
}
async function statOptional(filePath) {
  try {
    return await import_fs.promises.stat(filePath);
  } catch {
    return;
  }
}
function parseJSON(content) {
  let trimmed = content.trim();
  if (!trimmed) throw new Error("LLM returned empty content");
  try {
    return JSON.parse(trimmed);
  } catch {
    let start = trimmed.indexOf("{"), end = trimmed.lastIndexOf("}");
    if (start >= 0 && end > start) return JSON.parse(trimmed.slice(start, end + 1));
    throw new Error(`LLM did not return JSON: ${trimmed.slice(0, 160)}`);
  }
}
function buildParameterInfo(cdi3) {
  let groups = /* @__PURE__ */ new Map();
  for (let group of cdi3?.ParameterGroups ?? [])
    group.Id && groups.set(group.Id, group.Name ?? "");
  return (cdi3?.Parameters ?? []).filter((parameter) => !!parameter.Id).map((parameter) => ({
    id: parameter.Id,
    name: parameter.Name ?? "",
    groupId: parameter.GroupId ?? "",
    groupName: parameter.GroupId ? groups.get(parameter.GroupId) ?? "" : ""
  }));
}
function ruleForTarget(target, mode, scale, min, max) {
  return target ? { target, mode, scale, min, max } : void 0;
}
function ruleForTargets(targets, mode, scale, min, max) {
  let uniqueTargets = unique(targets);
  return uniqueTargets.length ? { targets: uniqueTargets, mode, scale, min, max } : void 0;
}
function sanitizePrivateEmotionMap(value, allowedTargets, fallback, source, blockedTargets = /* @__PURE__ */ new Set()) {
  let result = { ...fallback };
  if (!value || typeof value != "object" || Array.isArray(value)) return result;
  for (let [rawKey, rawMapping] of Object.entries(value)) {
    let key = rawKey.trim().slice(0, 80);
    if (!key) continue;
    if (rawMapping === null) {
      delete result[key];
      continue;
    }
    let mapping = sanitizePrivateEmotionMapping(rawMapping, allowedTargets, source, blockedTargets);
    mapping && (result[key] = mapping);
  }
  return result;
}
function sanitizePrivateEmotionMapping(value, allowedTargets, source, blockedTargets) {
  if (!value || typeof value != "object" || Array.isArray(value)) return;
  let record = value, target = typeof record.target == "string" && allowedTargets.has(record.target) && !blockedTargets.has(record.target) ? record.target : void 0, targets = Array.isArray(record.targets) ? unique(record.targets.filter((entry) => typeof entry == "string" && allowedTargets.has(entry) && !blockedTargets.has(entry))) : [];
  if (!target && targets.length === 0) return;
  let category = isPrivateEmotionCategory(record.category) ? record.category : void 0, emotions = Array.isArray(record.emotions) ? unique(record.emotions.filter((emotion) => typeof emotion == "string" && !!emotion.trim()).map((emotion) => emotion.trim().slice(0, 48))).slice(0, 16) : [], vadRange = sanitizePrivateEmotionVADRange(record.vadRange), triggerMode = record.triggerMode === "all" ? "all" : record.triggerMode === "any" ? "any" : void 0, activeValue = finiteOptionalNumber(record.activeValue), neutralValue = finiteOptionalNumber(record.neutralValue), intensity = boundedOptionalNumber(record.intensity, 0, 1), priority = boundedOptionalNumber(record.priority, -1e3, 1e3), exclusiveGroup = typeof record.exclusiveGroup == "string" && record.exclusiveGroup.trim() ? record.exclusiveGroup.trim().slice(0, 80) : void 0, confidence = boundedOptionalNumber(record.confidence, 0, 1) ?? (source === "llm" ? 0.65 : source === "manual" ? 1 : 0.8);
  return {
    ...target ? { target } : {},
    ...targets.length ? { targets } : {},
    category: category ?? "privateEffect",
    ...emotions.length ? { emotions } : {},
    ...vadRange ? { vadRange } : {},
    ...triggerMode ? { triggerMode } : {},
    ...activeValue !== void 0 ? { activeValue } : {},
    ...neutralValue !== void 0 ? { neutralValue } : {},
    ...intensity !== void 0 ? { intensity } : {},
    ...priority !== void 0 ? { priority } : {},
    ...exclusiveGroup ? { exclusiveGroup } : {},
    source,
    confidence
  };
}
function sanitizePrivateEmotionVADRange(value) {
  if (!value || typeof value != "object" || Array.isArray(value)) return;
  let record = value, result = {};
  for (let axis of ["valence", "arousal", "dominance"]) {
    let pair = finiteNumberPair(record[axis]);
    if (!pair) continue;
    let first = Math.max(-1, Math.min(1, pair[0])), second = Math.max(-1, Math.min(1, pair[1]));
    result[axis] = [Math.min(first, second), Math.max(first, second)];
  }
  return Object.keys(result).length ? result : void 0;
}
function isPrivateEmotionCategory(value) {
  return [
    "positiveEye",
    "blush",
    "tear",
    "shadow",
    "anger",
    "sweat",
    "surprise",
    "privateEffect"
  ].includes(String(value));
}
function boundedOptionalNumber(value, min, max) {
  let number = finiteOptionalNumber(value);
  return number === void 0 ? void 0 : Math.max(min, Math.min(max, number));
}
function sanitizeRule(value, allowedTargets) {
  if (!value || typeof value != "object") return;
  let record = value, target = typeof record.target == "string" && allowedTargets.has(record.target) ? record.target : void 0, targets = Array.isArray(record.targets) ? unique(record.targets.filter((item) => typeof item == "string" && allowedTargets.has(item))) : [];
  if (!target && targets.length === 0) return;
  let mode = isBlendMode(record.mode) ? record.mode : "set", scale = finiteNumber(record.scale, 1), offset = finiteOptionalNumber(record.offset), min = finiteOptionalNumber(record.min), max = finiteOptionalNumber(record.max), curve = isCurve(record.curve) ? record.curve : void 0, gamma = typeof record.gamma == "number" && Number.isFinite(record.gamma) && record.gamma > 0 ? record.gamma : void 0, deadzone = typeof record.deadzone == "number" && Number.isFinite(record.deadzone) && record.deadzone >= 0 ? record.deadzone : void 0, inputRange = finiteNumberPair(record.inputRange), outputRange = finiteNumberPair(record.outputRange), invertAround = finiteOptionalNumber(record.invertAround);
  return {
    ...target ? { target } : {},
    ...targets.length ? { targets } : {},
    mode,
    scale,
    ...offset !== void 0 ? { offset } : {},
    ...min !== void 0 ? { min } : {},
    ...max !== void 0 ? { max } : {},
    ...curve !== void 0 ? { curve } : {},
    ...gamma !== void 0 ? { gamma } : {},
    ...deadzone !== void 0 ? { deadzone } : {},
    ...inputRange !== void 0 ? { inputRange } : {},
    ...outputRange !== void 0 ? { outputRange } : {},
    ...invertAround !== void 0 ? { invertAround } : {}
  };
}
function sanitizeNumericRecord(value, allowedKeys) {
  if (!value || typeof value != "object") return {};
  let result = {};
  for (let [key, raw] of Object.entries(value))
    allowedKeys.has(key) && typeof raw == "number" && Number.isFinite(raw) && (result[key] = raw);
  return result;
}
function isBlendMode(value) {
  return value === "set" || value === "add" || value === "subtract" || value === "inverse";
}
function isCurve(value) {
  return value === "linear" || value === "easeIn" || value === "easeOut" || value === "easeInOut" || value === "smoothstep";
}
function isMotionPriority(value) {
  return value === "idle" || value === "normal" || value === "force";
}
function finiteOptionalNumber(value) {
  return typeof value == "number" && Number.isFinite(value) ? value : void 0;
}
function finiteNumberPair(value) {
  if (!Array.isArray(value) || value.length !== 2) return;
  let first = finiteOptionalNumber(value[0]), second = finiteOptionalNumber(value[1]);
  return first !== void 0 && second !== void 0 ? [first, second] : void 0;
}
function emptyCapabilities() {
  return {
    headControl: !1,
    bodyControl: !1,
    eyeBlink: !1,
    eyeSmile: !1,
    gazeControl: !1,
    mouthOpen: !1,
    mouthSmile: !1,
    browControl: !1,
    blush: !1,
    tear: !1,
    sweat: !1,
    breath: !1
  };
}
function sanitizeModelDir(input) {
  let normalized = input.trim() || "lilyabee";
  if (!/^[a-zA-Z0-9_-]+$/u.test(normalized))
    throw new Error("modelDir may only contain letters, numbers, underscore, and dash");
  return normalized;
}
function sanitizeId(input) {
  return input.replace(/[^a-zA-Z0-9_-]/gu, "_").toLowerCase();
}
function normalizeRelativeFile(input) {
  return input.replace(/\\/gu, "/").replace(/^\/+/u, "");
}
function resolveModelFile(directoryPath, relativeFile) {
  let resolved = import_path.default.resolve(directoryPath, normalizeRelativeFile(relativeFile));
  if (!isInside(directoryPath, resolved))
    throw new Error(`Model file reference escapes its model directory: ${relativeFile}`);
  return resolved;
}
function normalizeText(input) {
  return input.replace(/\s+/gu, "").replace(/[＿_\-　]/gu, "").toLowerCase();
}
function isMouthOpenLive2DParameter(parameter) {
  let idAndName = normalizeText(`${parameter.id} ${parameter.name}`);
  return [
    "mouthform",
    "mouthshape",
    "lipshape",
    "lipform",
    "liptype",
    "\u5634\u578B",
    "\u53E3\u578B",
    "\u5507\u5F62",
    "\u5507\u578B"
  ].some((hint) => idAndName.includes(normalizeText(hint))) ? !1 : [
    "mouthopen",
    "openmouth",
    "jawopen",
    "openjaw",
    "\u5634\u5F20\u5F00",
    "\u5F20\u5634",
    "\u5634\u5DF4\u5F00\u5408",
    "\u5634\u5F00\u5408",
    "\u53E3\u90E8\u5F00\u5408",
    "\u4E0B\u988C\u5F00\u5408"
  ].some((hint) => idAndName.includes(normalizeText(hint)));
}
function matchesSemanticNeedle(parameter, rawNeedle) {
  let needle = normalizeText(rawNeedle);
  if (!needle) return !1;
  let fields = [parameter.id, parameter.name, parameter.groupName].filter(Boolean);
  if (/[^\u0000-\u007f]/u.test(needle))
    return fields.some((field) => normalizeText(field).includes(needle));
  let needleTokens = semanticTokens(rawNeedle);
  return fields.some((field) => {
    let tokens = semanticTokens(field);
    return needleTokens.every((needleToken) => tokens.some((token) => token.startsWith(needleToken)));
  });
}
function matchesSelectorNeedle(parameter, rawNeedle) {
  let needle = normalizeText(rawNeedle);
  if (!needle) return !1;
  let fields = [parameter.id, parameter.name, parameter.groupName].filter(Boolean);
  return /^[a-z]$/u.test(needle) ? fields.some((field) => semanticTokens(field).includes(needle)) : fields.some((field) => normalizeText(field).includes(needle));
}
function semanticTokens(input) {
  return input.replace(/([a-z0-9])([A-Z])/gu, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/gu, "$1 $2").toLowerCase().split(/[^a-z0-9]+/gu).filter(Boolean);
}
function toWebPath(input) {
  return input.replace(/\\/gu, "/");
}
function normalizeModelsBaseUrl(input) {
  let trimmed = input.trim();
  return !trimmed || trimmed === "/" ? "" : trimmed.replace(/\/+$/u, "");
}
function joinModelsUrl(baseUrl, ...segments) {
  let suffix = segments.map((segment) => segment.replace(/^\/+|\/+$/gu, "")).join("/");
  return `${baseUrl}/${suffix}`;
}
function isInside(root, candidate) {
  let relative = import_path.default.relative(import_path.default.resolve(root), import_path.default.resolve(candidate));
  return !!relative && !relative.startsWith("..") && !import_path.default.isAbsolute(relative);
}
function unique(values) {
  return [...new Set(values)];
}
function finiteNumber(value, fallback) {
  return typeof value == "number" && Number.isFinite(value) ? value : fallback;
}
function stringOr(value, fallback) {
  return typeof value == "string" && value.trim() ? value.trim() : fallback;
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  Live2DProfileAutoGenerator,
  STANDARD_PARAM_TABLE,
  profileGeneratorVersion,
  resolveStandard,
  validateModelProfile
});
