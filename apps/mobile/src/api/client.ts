/**
 * Axios API client for the Sonolo backend.
 *
 * - Base URL from EXPO_PUBLIC_API_URL (set in .env for each environment),
 *   defaulting to the local FastAPI dev server.
 * - Request interceptor attaches `Authorization: Bearer <token>` from the
 *   in-memory token set by the auth store.
 * - Response interceptor catches 401s, clears the token, and invokes the
 *   unauthorized handler registered by the auth store (which logs out and
 *   triggers the redirect to login via state-driven routing).
 * - Response interceptor also detects network-level failures (no HTTP
 *   response at all) and notifies connectivity handlers so the UI can
 *   show an offline banner; the first successful response reports the
 *   connection is back.
 *
 * The handler-injection pattern keeps this module free of store imports,
 * avoiding a circular dependency (authStore -> client -> authStore).
 */

import axios, { AxiosError, type AxiosInstance } from "axios";

const processEnv: Record<string, string | undefined> | undefined = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env;

export const API_BASE_URL: string =
  processEnv?.EXPO_PUBLIC_API_URL ?? "http://192.168.1.228:8000";

/** Authenticated voice WebSocket URL (backend mounts /ws outside /api). */
export function voiceSocketUrl(sessionId: string, token: string): string {
  const wsBase = API_BASE_URL.replace(/^http/, "ws");
  return `${wsBase}/ws/voice/${sessionId}?token=${encodeURIComponent(token)}`;
}

let authToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

/** Set (or clear) the token attached to outgoing requests. */
export function setAuthToken(token: string | null): void {
  authToken = token;
}

/** Register the callback fired when any request comes back 401. */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

let offlineHandler: (() => void) | null = null;
let onlineHandler: (() => void) | null = null;
/** Tracks the last known connectivity so handlers fire on transitions only. */
let wasOffline = false;

/**
 * True when a request failed without receiving any HTTP response —
 * DNS failure, refused socket, timeout — i.e. the server never spoke.
 * Canceled requests and real HTTP error responses are not outages.
 */
export function isNetworkError(error: unknown): boolean {
  return (
    axios.isAxiosError(error) &&
    !axios.isCancel(error) &&
    error.response === undefined &&
    error.request !== undefined
  );
}

/**
 * Register callbacks fired when the API becomes unreachable (once per
 * outage) and reachable again (once per recovery). Pass nulls or an
 * empty object to clear.
 */
export function setConnectivityHandlers(handlers: {
  onOffline?: (() => void) | null;
  onOnline?: (() => void) | null;
}): void {
  offlineHandler = handlers.onOffline ?? null;
  onlineHandler = handlers.onOnline ?? null;
}

/** Reset transition tracking; used after logout and in tests. */
export function resetConnectivityState(): void {
  wasOffline = false;
}

export const api: AxiosInstance = axios.create({
  baseURL: `${API_BASE_URL}/api`,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (authToken !== null) {
    config.headers.set("Authorization", `Bearer ${authToken}`);
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    if (wasOffline) {
      wasOffline = false;
      onlineHandler?.();
    }
    return response;
  },
  (error: AxiosError) => {
    if (isNetworkError(error)) {
      if (!wasOffline) {
        wasOffline = true;
        offlineHandler?.();
      }
    } else if (wasOffline) {
      // A real HTTP answer means the network is fine again.
      wasOffline = false;
      onlineHandler?.();
    }
    if (error.response?.status === 401) {
      authToken = null;
      unauthorizedHandler?.();
    }
    return Promise.reject(error);
  },
);

// ---------------------------------------------------------------------
// Backend payload types (mirrors of the FastAPI schemas)
// ---------------------------------------------------------------------

/** Content languages the backend catalogs support (SN-020). */
export type PreferredLanguage = "en" | "fr";

export interface UserSkill {
  fluency_score: number;
  pronunciation_score: number;
  grammar_score: number;
  vocabulary_score: number;
  coherence_score: number;
  task_completion_score: number;
  composite_score: number;
  canada_ready_score: number;
  confidence_score: number;
  updated_at: string;
}

export interface User {
  id: string;
  email: string | null;
  name: string;
  native_language: string;
  target_language: string;
  learning_goal: string;
  current_level: string;
  /** Content language driving the scenario catalog (SN-020). */
  preferred_language: PreferredLanguage;
  subscription_tier: string;
  streak_count: number;
  streak_last_date: string | null;
  total_xp: number;
  total_speaking_seconds: number;
  onboarding_completed: boolean;
  created_at: string;
  skills: UserSkill | null;
}

export interface RegisterPayload {
  name: string;
  email: string;
  password: string;
  native_language: string;
  target_language: string;
}

export interface TokenPayload {
  access_token: string;
  token_type: string;
}

// ---------------------------------------------------------------------
// Endpoint helpers
// ---------------------------------------------------------------------

export async function loginRequest(
  email: string,
  password: string,
): Promise<TokenPayload> {
  const { data } = await api.post<TokenPayload>("/auth/login", {
    email,
    password,
  });
  return data;
}

export async function registerRequest(
  payload: RegisterPayload,
): Promise<User> {
  const { data } = await api.post<User>("/auth/register", payload);
  return data;
}

export async function fetchCurrentUser(): Promise<User> {
  const { data } = await api.get<User>("/users/me");
  return data;
}

/** Map any thrown error to a calm, user-facing message. */
export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response) {
      const detail: unknown = error.response.data?.detail;
      if (typeof detail === "string") {
        return detail;
      }
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as { msg?: string } | undefined;
        if (typeof first?.msg === "string") {
          return first.msg;
        }
      }
      if (error.response.status === 401) {
        return "Incorrect email or password.";
      }
      return `Request failed (${error.response.status}).`;
    }
    return "Can't reach Sonolo — check your connection and the server.";
  }
  return "Something went wrong. Please try again.";
}

// ---------------------------------------------------------------------
// Scenarios (SN-015)
// ---------------------------------------------------------------------

export interface Scenario {
  id: string;
  title: string;
  description: string;
  category: string;
  /** BCP-47 content language of the pack this scenario belongs to. */
  target_language?: string;
  /** Manifest pack this scenario belongs to (SN-035); absent on
   *  catalogs cached before the field shipped. */
  pack_id?: string;
  difficulty: number | null;
  /** True when premium content is gated for this caller (SN-026). */
  is_locked?: boolean;
}

export async function fetchScenarios(
  language?: PreferredLanguage,
): Promise<Scenario[]> {
  const { data } = await api.get<{ scenarios: Scenario[] }>("/scenarios", {
    params: language === undefined ? undefined : { language },
  });
  return data.scenarios;
}

/**
 * POST /users/me/upgrade — mock tier flip to premium (SN-026).
 * Stand-in for the real RevenueCat purchase flow.
 */
export async function upgradeAccountRequest(): Promise<User> {
  const { data } = await api.post<User>("/users/me/upgrade");
  return data;
}

/**
 * POST /users/me/language — persist the content language (SN-020).
 * Returns the updated profile; callers should refetch the scenario
 * catalog with the new language afterwards.
 */
export async function updatePreferredLanguage(
  language: PreferredLanguage,
): Promise<User> {
  const { data } = await api.post<User>("/users/me/language", { language });
  return data;
}

// ---------------------------------------------------------------------
// Content packs (SN-030)
// ---------------------------------------------------------------------

/** One manifest pack as rendered by the Learn tab's pack cards. */
export interface ContentPack {
  id: string;
  /** Manifest pack type; the Learn tab renders "scenarios" packs. */
  type: string;
  title: string;
  description: string;
  category: string;
  language: string;
  tier: string;
  theme_color: string;
  icon: string;
  /** Live catalog stats keyed on Scenario.pack_id (SN-035); absent
   *  from responses served before the field shipped. */
  scenario_count?: number;
  premium_count?: number;
}

/** GET /packs — manifest-declared scenario packs for the Learn tab. */
export async function fetchPacks(): Promise<ContentPack[]> {
  const { data } = await api.get<{ packs: ContentPack[] }>("/packs");
  return data.packs;
}

// ---------------------------------------------------------------------
// Culture Corner micro-lessons (SN-047)
// ---------------------------------------------------------------------

/** One micro-lesson as shown in the Learn tab's Culture Corner rail. */
export interface MicrolessonSummary {
  id: string;
  title: string;
  hook: string;
  read_minutes: number;
  /** Manifest pack the lesson belongs to (SN-047); absent on catalogs
   *  cached before the format shipped. */
  pack_id?: string;
  /** Pack theme color for card theming (SN-047). */
  theme_color?: string;
  /** Pack icon (emoji, e.g. 🍁) rendered on the card. */
  icon?: string;
}

/** One headed paragraph inside a micro-lesson. */
export interface MicrolessonSection {
  heading: string;
  text: string;
}

/** The full lesson body for the reader screen. */
export interface Microlesson extends MicrolessonSummary {
  sections: MicrolessonSection[];
  takeaway: string;
  try_it: string;
}

/** GET /microlessons — Culture Corner summaries for the Learn rail.
 *  An optional language ("en" | "fr") filters to that pack (SN-049);
 *  omitted, every lesson is returned. */
export async function fetchMicrolessons(
  language?: PreferredLanguage,
): Promise<MicrolessonSummary[]> {
  const { data } = await api.get<{ microlessons: MicrolessonSummary[] }>(
    "/microlessons",
    { params: language === undefined ? undefined : { language } },
  );
  return data.microlessons;
}

/** GET /microlessons/{id} — one full micro-lesson for the reader. */
export async function fetchMicrolesson(id: string): Promise<Microlesson> {
  const { data } = await api.get<Microlesson>(`/microlessons/${id}`);
  return data;
}

// ---------------------------------------------------------------------
// CanadaReady™ Scorecard (SN-048)
// ---------------------------------------------------------------------

/** The badge tier earned at the learner's current CanadaReady score. */
export interface ScorecardBadge {
  code: string;
  title: string;
  tagline: string;
}

/** One of the six speaking-readiness bands with its CLB-inspired hint. */
export interface ScorecardBand {
  code: string;
  label: string;
  /** 0–100 dimension score. */
  score: number;
  clb_hint: string;
}

/** Session and engagement stats shown under the bands. */
export interface ScorecardStats {
  sessions_completed: number;
  speaking_minutes: number;
  streak_current: number;
  total_xp: number;
}

/** The CanadaReady™ Scorecard payload from GET /users/me/scorecard. */
export interface Scorecard {
  generated_at: string;
  badge: ScorecardBadge;
  /** 0–100 overall readiness score. */
  canada_ready_score: number;
  bands: ScorecardBand[];
  stats: ScorecardStats;
  disclaimer: string;
}

/** GET /users/me/scorecard — the caller's CanadaReady™ Scorecard. */
export async function fetchScorecard(): Promise<Scorecard> {
  const { data } = await api.get<Scorecard>("/users/me/scorecard");
  return data;
}

/**
 * Authenticated PDF download: fetches the scorecard PDF as raw bytes
 * (the axios interceptor attaches the Bearer token) and returns them
 * as a base64 string for expo-file-system to write to disk.
 */
export async function fetchScorecardPdf(): Promise<string> {
  const { data } = await api.get<ArrayBuffer>("/users/me/scorecard/pdf", {
    responseType: "arraybuffer",
  });
  const bytes = new Uint8Array(data);
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

// ---------------------------------------------------------------------
// Pronunciation Lab (SN-049)
// ---------------------------------------------------------------------

/** One pronunciation drill as shown in the Learn tab's Lab rail. */
export interface PronunciationDrillSummary {
  id: string;
  title: string;
  focus: string;
  level: string;
  is_premium: boolean;
  /** True when the drill is premium and the caller is on the free tier. */
  is_locked?: boolean;
  theme_color?: string;
  icon?: string;
}

/** The full drill body for the player screen. */
export interface PronunciationDrill {
  id: string;
  title: string;
  focus: string;
  target_sentence: string;
  target_words: string[];
  ipa_hint: string;
  tip: string;
  level: string;
  is_premium: boolean;
  pack_id: string;
  theme_color: string;
  icon: string;
}

/** One phoneme's deterministic mock score and tip. */
export interface PhonemeScore {
  symbol: string;
  score: number;
  tip: string;
}

/** The deterministic mock evaluation returned by the backend. */
export interface PronunciationEvaluation {
  overall: number;
  phonemes: PhonemeScore[];
  fluency_score: number;
  tip_summary: string;
  engine_version: string;
}

/** GET /pronunciation/drills — the Pronunciation Lab catalog. */
export async function fetchPronunciationDrills(): Promise<
  PronunciationDrillSummary[]
> {
  const { data } = await api.get<{ drills: PronunciationDrillSummary[] }>(
    "/pronunciation/drills",
  );
  return data.drills;
}

/** GET /pronunciation/drills/{id} — one full drill for the player. */
export async function fetchPronunciationDrill(
  id: string,
): Promise<PronunciationDrill> {
  const { data } = await api.get<PronunciationDrill>(
    `/pronunciation/drills/${id}`,
  );
  return data;
}

/** POST /pronunciation/drills/{id}/evaluate — score one take (mock). */
export async function evaluatePronunciation(
  id: string,
  durationSeconds: number,
): Promise<PronunciationEvaluation> {
  const { data } = await api.post<PronunciationEvaluation>(
    `/pronunciation/drills/${id}/evaluate`,
    { duration_seconds: durationSeconds },
  );
  return data;
}

// ---------------------------------------------------------------------
// Listening Gym (SN-050)
// ---------------------------------------------------------------------

/** One listening dialogue as shown in the Learn tab's Gym rail. */
export interface ListeningDialogueSummary {
  id: string;
  title: string;
  context: string;
  level: string;
  difficulty: number;
  listening_focus: string;
  is_premium: boolean;
  /** True when the dialogue is premium and the caller is on the free tier. */
  is_locked?: boolean;
  theme_color?: string;
  icon?: string;
}

/** One spoken turn inside a dialogue. */
export interface DialogueTurn {
  role: "speaker" | "listener" | "system";
  text: string;
  pause_after_ms: number;
}

/** One comprehension question with four choices. */
export interface ListeningQuestion {
  prompt: string;
  choices: string[];
  correct_index: number;
  explanation: string;
}

/** The full dialogue body for the player screen. */
export interface ListeningDialogue {
  id: string;
  title: string;
  context: string;
  level: string;
  difficulty: number;
  listening_focus: string;
  is_premium: boolean;
  turns: DialogueTurn[];
  questions: ListeningQuestion[];
  vocab_targets: string[];
  pack_id: string;
  theme_color: string;
  icon: string;
}

/** One incorrectly answered question in the results. */
export interface MissedQuestion {
  prompt: string;
  your_answer: string;
  correct_answer: string;
  explanation: string;
}

/** The deterministic mock evaluation returned by the backend. */
export interface ListeningEvaluation {
  correct_count: number;
  total: number;
  score: number;
  missed: MissedQuestion[];
  time_seconds: number;
  engine_version: string;
}

/** GET /listening/dialogues — the Listening Gym catalog. */
export async function fetchListeningDialogues(): Promise<
  ListeningDialogueSummary[]
> {
  const { data } = await api.get<{ dialogues: ListeningDialogueSummary[] }>(
    "/listening/dialogues",
  );
  return data.dialogues;
}

/** GET /listening/dialogues/{id} — one full dialogue for the player. */
export async function fetchListeningDialogue(
  id: string,
): Promise<ListeningDialogue> {
  const { data } = await api.get<ListeningDialogue>(
    `/listening/dialogues/${id}`,
  );
  return data;
}

/** POST /listening/dialogues/{id}/evaluate — score a quiz take (mock). */
export async function evaluateListening(
  id: string,
  answers: number[],
  timeSeconds: number,
): Promise<ListeningEvaluation> {
  const { data } = await api.post<ListeningEvaluation>(
    `/listening/dialogues/${id}/evaluate`,
    { answers, time_seconds: timeSeconds },
  );
  return data;
}

// ---------------------------------------------------------------------
// Entitlements (SN-041)
// ---------------------------------------------------------------------

/** Server-side access entitlements for the current caller. */
export interface Entitlements {
  tier: string;
  premium_scenario_ids: string[];
  expires_at: string | null;
}

/** GET /users/me/entitlements — what premium scenarios the caller may complete. */
export async function fetchEntitlements(): Promise<Entitlements> {
  const { data } = await api.get<Entitlements>("/users/me/entitlements");
  return data;
}

// ---------------------------------------------------------------------
// Journey Map (C8)
// ---------------------------------------------------------------------

export type JourneyBandStatus = "locked" | "active" | "completed";
export type JourneyUnitStatus = "locked" | "current" | "completed";
export type JourneySkillStatus =
  | "locked"
  | "complete"
  | "in_progress"
  | "not_started";

export interface JourneySkill {
  skill: string;
  status: JourneySkillStatus;
}

export interface JourneyUnit {
  id: string;
  title: string;
  status: JourneyUnitStatus;
  skills: JourneySkill[];
}

export interface JourneyBand {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
  status: JourneyBandStatus;
  expanded: boolean;
  unlock_condition: string | null;
  units: JourneyUnit[];
}

export interface JourneyMapData {
  current_unit_id: string | null;
  bands: JourneyBand[];
}

/** GET /learn/journey — band/unit map from C0 unit progress. */
export async function fetchJourney(): Promise<JourneyMapData> {
  const { data } = await api.get<JourneyMapData>("/learn/journey");
  return data;
}

// ---------------------------------------------------------------------
// Unit catalog (C1) — used by C10 Unit Detail
// ---------------------------------------------------------------------

export interface UnitReadingActivity {
  id: string;
  type: string;
}

export interface UnitDetail {
  id: string;
  band: string;
  title: string;
  story_chapter: string;
  theme: string;
  icon: string;
  level_target: number;
  language: string;
  vocabulary_targets: string[];
  grammar_targets: string[];
  reading_ids: string[];
  writing_ids: string[];
  listening_ids: string[];
  speaking_ids: string[];
  reading_required_activities: UnitReadingActivity[];
  reading_optional_activities: UnitReadingActivity[];
  unit_test_id: string | null;
  is_published: boolean;
}

/** GET /learn/units/{code} — published unit catalog (C1). */
export async function fetchUnit(unitCode: string): Promise<UnitDetail> {
  const { data } = await api.get<UnitDetail>(
    `/learn/units/${encodeURIComponent(unitCode)}`,
  );
  return data;
}

// ---------------------------------------------------------------------
// Four-skill progress (C9) — C2 levels, not XP / current_level
// ---------------------------------------------------------------------

export type SkillImbalancePriority = "critical" | "high" | "balanced";

export interface SkillLevel {
  skill: string;
  level: number;
}

export interface SkillImbalance {
  priority: SkillImbalancePriority;
  skill: string | null;
  message: string;
  daily_mix_weight: number | null;
}

export interface SkillProgress {
  skills: SkillLevel[];
  display_level: number;
  readiness_level: number;
  imbalance: SkillImbalance;
}

/** GET /progress/skills — C2 four-skill levels, display, readiness, §5.6. */
export async function fetchSkillProgress(): Promise<SkillProgress> {
  const { data } = await api.get<SkillProgress>("/progress/skills");
  return data;
}

// ---------------------------------------------------------------------
// Daily quests & gamification summary (SN-017)
// ---------------------------------------------------------------------

export interface TodayQuestsResponse {
  quest_date: string;
  timezone: string;
  quests: QuestResult[];
}

/** GET /quests/today — lazily generates the user's three daily quests. */
export async function fetchTodayQuests(): Promise<TodayQuestsResponse> {
  const { data } = await api.get<TodayQuestsResponse>("/quests/today");
  return data;
}

export interface GamificationSummary {
  xp_total: number;
  xp_today: number;
  xp_today_date: string | null;
  level: number;
  progress_to_next_level: number;
  next_level_xp_threshold: number;
  xp_into_level: number;
  current_streak: number;
  longest_streak: number;
  last_activity_at: string | null;
  last_activity_local_date: string | null;
  badges: BadgeResult[];
}

/** GET /gamification/me — read-only XP, level, streak, badge snapshot. */
export async function fetchGamificationSummary(): Promise<GamificationSummary> {
  const { data } = await api.get<GamificationSummary>("/gamification/me");
  return data;
}

// ---------------------------------------------------------------------
// Session completion (SN-015 -> SN-014 contract)
// ---------------------------------------------------------------------

export interface TranscriptTurnInput {
  role: "user" | "assistant" | "system";
  text: string;
}

export interface EvaluationScoresInput {
  fluency: number;
  pronunciation: number;
  grammar: number;
  vocabulary: number;
  coherence: number;
  task_completion: number;
}

export interface EvaluationInput {
  scores: EvaluationScoresInput;
  overall_score: number;
  insights: string[];
  engine_version: string;
}

export interface SessionCompletePayload {
  client_session_id: string;
  scenario_id: string;
  started_at: string;
  ended_at: string;
  duration_seconds: number;
  transcript: TranscriptTurnInput[];
  evaluation: EvaluationInput;
  client_info?: Record<string, string>;
}

export interface SkillUpdate {
  dimension: string;
  previous_score: number | null;
  session_score: number;
  new_score: number;
}

export interface QuestResult {
  code: string;
  title: string;
  description: string;
  target_count: number;
  progress_count: number;
  reward_xp: number;
  completed: boolean;
}

export interface BadgeResult {
  code: string;
  title: string;
  description: string;
  awarded_at: string;
}

export interface SessionCompleteResponse {
  session_id: string;
  idempotent_replayed: boolean;
  xp_eligible: boolean;
  xp: {
    session_xp: number;
    quest_xp: number;
    total_xp: number;
    xp_total: number;
    xp_today: number;
    level: number;
    progress_to_next_level: number;
  };
  skills: SkillUpdate[];
  streak_current: number;
  streak_longest: number;
  quests: QuestResult[];
  newly_awarded_badges: BadgeResult[];
  completed_at: string;
}

/**
 * Deterministic placeholder evaluation until the real evaluator feeds
 * the completion call — valid per the backend's Pydantic contract, so
 * sessions persist and gamification is awarded.
 */
export function buildMockEvaluation(): EvaluationInput {
  return {
    scores: {
      fluency: 75,
      pronunciation: 75,
      grammar: 75,
      vocabulary: 75,
      coherence: 75,
      task_completion: 75,
    },
    overall_score: 75,
    insights: [],
    engine_version: "sn011-deterministic-v1",
  };
}

export async function completeSession(
  payload: SessionCompletePayload,
): Promise<SessionCompleteResponse> {
  const { data } = await api.post<SessionCompleteResponse>(
    "/sessions/complete",
    payload,
  );
  return data;
}
