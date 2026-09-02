/**
 * C9 Progress UI v2: four-skill levels from GET /progress/skills (C2),
 * imbalance alert from the C2 payload, existing XP/streak/badges/
 * CanadaReady remain.
 */

const mockRouter = {
  replace: jest.fn(),
  push: jest.fn(),
  navigate: jest.fn(),
};

jest.mock("expo-router", () => ({
  useRouter: () => mockRouter,
}));

jest.mock("react-native-reanimated", () => {
  const { View } = require("react-native");
  return {
    __esModule: true,
    default: { View },
    Easing: {
      out: (easing: unknown) => easing,
      inOut: (easing: unknown) => easing,
      quad: (t: number) => t,
    },
    useSharedValue: (value: number) => ({ value }),
    useAnimatedStyle: (
      builder: (shared: { value: number }) => Record<string, unknown>,
    ) => builder({ value: 0 }),
    withTiming: (value: number) => value,
    withDelay: (_delay: number, value: number) => value,
    withRepeat: (value: number) => value,
  };
});

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

jest.mock("lucide-react-native", () => ({
  Award: () => null,
  ChevronRight: () => null,
  Flame: () => null,
  RefreshCw: () => null,
  Trophy: () => null,
  Zap: () => null,
}));

jest.mock("../../src/api/client", () => {
  const actual = jest.requireActual("../../src/api/client");
  return {
    ...actual,
    fetchGamificationSummary: jest.fn(),
    fetchScorecard: jest.fn(),
    fetchSkillProgress: jest.fn(),
  };
});

import { fireEvent, render, waitFor } from "@testing-library/react-native";

import ProgressScreen from "../../app/(tabs)/progress";
import {
  fetchGamificationSummary,
  fetchScorecard,
  fetchSkillProgress,
  type SkillProgress,
} from "../../src/api/client";
import { FourSkillCard } from "../../src/components/progress/FourSkillCard";
import { useAuthStore } from "../../src/stores/authStore";

const mockFetchSkills = fetchSkillProgress as jest.Mock;
const mockFetchSummary = fetchGamificationSummary as jest.Mock;
const mockFetchScorecard = fetchScorecard as jest.Mock;

/** Exact C2 §5.6 messages from mastery_service.get_skill_recommendation. */
const C2_CRITICAL_MESSAGE =
  "Your writing (Level 3) is holding back your readiness. Focus on writing to unlock your potential.";
const C2_HIGH_MESSAGE =
  "Boost your writing to catch up with your other skills.";
const C2_BALANCED_MESSAGE = "Your skills are well-balanced! Keep it up.";

function criticalPayload(): SkillProgress {
  return {
    skills: [
      { skill: "speaking", level: 5 },
      { skill: "listening", level: 4 },
      { skill: "reading", level: 6 },
      { skill: "writing", level: 3 },
    ],
    display_level: 4,
    readiness_level: 3,
    imbalance: {
      priority: "critical",
      skill: "writing",
      message: C2_CRITICAL_MESSAGE,
      daily_mix_weight: 0.5,
    },
  };
}

function highPayload(): SkillProgress {
  return {
    skills: [
      { skill: "speaking", level: 5 },
      { skill: "listening", level: 5 },
      { skill: "reading", level: 5 },
      { skill: "writing", level: 3 },
    ],
    display_level: 4,
    readiness_level: 3,
    imbalance: {
      priority: "high",
      skill: "writing",
      message: C2_HIGH_MESSAGE,
      daily_mix_weight: 0.4,
    },
  };
}

function balancedPayload(): SkillProgress {
  return {
    skills: [
      { skill: "speaking", level: 5 },
      { skill: "listening", level: 5 },
      { skill: "reading", level: 5 },
      { skill: "writing", level: 5 },
    ],
    display_level: 5,
    readiness_level: 5,
    imbalance: {
      priority: "balanced",
      skill: null,
      message: C2_BALANCED_MESSAGE,
      daily_mix_weight: null,
    },
  };
}

function gamification() {
  return {
    xp_total: 240,
    xp_today: 20,
    xp_today_date: "2026-09-02",
    level: 2,
    progress_to_next_level: 40,
    next_level_xp_threshold: 100,
    xp_into_level: 40,
    current_streak: 4,
    longest_streak: 7,
    last_activity_at: null,
    last_activity_local_date: null,
    badges: [
      {
        code: "first-session",
        title: "First Session",
        description: "You spoke",
        awarded_at: "2026-09-01T12:00:00Z",
      },
    ],
  };
}

function scorecard() {
  return {
    generated_at: "2026-09-02T12:00:00Z",
    badge: {
      code: "first-steps",
      title: "First Steps",
      tagline: "On the way",
    },
    canada_ready_score: 42,
    bands: [],
    stats: {
      sessions_completed: 3,
      speaking_minutes: 12,
      streak_current: 4,
      total_xp: 240,
    },
    disclaimer: "Not an official CLB or CEFR certification.",
  };
}

function seedUser(): void {
  useAuthStore.setState({
    user: {
      id: "user-1",
      email: "pavan@example.com",
      name: "Pavan",
      native_language: "hi",
      target_language: "en-CA",
      learning_goal: "pr_readiness",
      current_level: "sprout",
      preferred_language: "en",
      subscription_tier: "free",
      streak_count: 4,
      streak_last_date: null,
      total_xp: 240,
      total_speaking_seconds: 0,
      onboarding_completed: true,
      created_at: "2026-09-02T12:00:00Z",
      skills: {
        fluency_score: 70,
        pronunciation_score: 68,
        grammar_score: 65,
        vocabulary_score: 72,
        coherence_score: 66,
        task_completion_score: 71,
        composite_score: 69,
        canada_ready_score: 42,
        confidence_score: 60,
        updated_at: "2026-09-02T12:00:00Z",
      },
    },
    token: "test-token",
    isLoading: false,
    isHydrated: true,
    isAuthenticated: true,
  });
}

async function renderProgress(skills: SkillProgress | Error) {
  seedUser();
  mockFetchSummary.mockResolvedValue(gamification());
  mockFetchScorecard.mockResolvedValue(scorecard());
  if (skills instanceof Error) {
    mockFetchSkills.mockRejectedValue(skills);
  } else {
    mockFetchSkills.mockResolvedValue(skills);
  }
  const screen = render(<ProgressScreen />);
  if (skills instanceof Error) {
    await waitFor(() => {
      expect(screen.getByText("CanadaReady™ Scorecard")).toBeTruthy();
      expect(screen.getByLabelText("Retry loading skill levels")).toBeTruthy();
    });
  } else {
    await waitFor(() => {
      expect(screen.getByText("CanadaReady™ Scorecard")).toBeTruthy();
      expect(
        screen.getByLabelText(`Display Level ${skills.display_level}`),
      ).toBeTruthy();
    });
  }
  return screen;
}

describe("FourSkillCard consumes the C2 API payload (C9)", () => {
  it("renders all four skills, Display Level, and Readiness Level", () => {
    const screen = render(<FourSkillCard progress={criticalPayload()} />);
    expect(screen.getByLabelText("Display Level 4")).toBeTruthy();
    expect(screen.getByLabelText("Readiness Level 3")).toBeTruthy();
    expect(screen.getByLabelText("Speaking Level 5 of 9")).toBeTruthy();
    expect(screen.getByLabelText("Listening Level 4 of 9")).toBeTruthy();
    expect(screen.getByLabelText("Reading Level 6 of 9")).toBeTruthy();
    expect(screen.getByLabelText("Writing Level 3 of 9")).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Four-skill radar. Speaking Level 5. Listening Level 4. Reading Level 6. Writing Level 3",
      ),
    ).toBeTruthy();
  });

  it("shows the C2 critical message and identifies writing as weakest", () => {
    const screen = render(<FourSkillCard progress={criticalPayload()} />);
    expect(screen.getByLabelText(C2_CRITICAL_MESSAGE)).toBeTruthy();
    expect(screen.getByText(C2_CRITICAL_MESSAGE)).toBeTruthy();
  });

  it("shows the C2 high message when gap is exactly 2", () => {
    const screen = render(<FourSkillCard progress={highPayload()} />);
    expect(screen.getByText(C2_HIGH_MESSAGE)).toBeTruthy();
    expect(screen.queryByText(C2_CRITICAL_MESSAGE)).toBeNull();
  });

  it("does not show an imbalance alert when C2 says balanced", () => {
    const screen = render(<FourSkillCard progress={balancedPayload()} />);
    expect(screen.queryByText(C2_BALANCED_MESSAGE)).toBeNull();
    expect(screen.queryByText(C2_HIGH_MESSAGE)).toBeNull();
    expect(screen.queryByText(C2_CRITICAL_MESSAGE)).toBeNull();
  });

  it("does not recompute imbalance locally — trusts the C2 priority field", () => {
    const spoofed: SkillProgress = {
      ...criticalPayload(),
      imbalance: {
        priority: "balanced",
        skill: null,
        message: C2_BALANCED_MESSAGE,
        daily_mix_weight: null,
      },
    };
    const screen = render(<FourSkillCard progress={spoofed} />);
    expect(screen.queryByText(C2_CRITICAL_MESSAGE)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Progress screen four-skill block alongside existing UI (C9)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("renders C2 skill levels with XP, streak, badges, and CanadaReady", async () => {
    const screen = await renderProgress(criticalPayload());
    expect(mockFetchSkills).toHaveBeenCalled();
    expect(screen.getByLabelText("Display Level 4")).toBeTruthy();
    expect(screen.getByLabelText("Readiness Level 3")).toBeTruthy();
    expect(screen.getByLabelText("Speaking Level 5 of 9")).toBeTruthy();
    expect(screen.getByLabelText("Writing Level 3 of 9")).toBeTruthy();
    expect(screen.getByText(C2_CRITICAL_MESSAGE)).toBeTruthy();
    expect(screen.getByText("240 XP total")).toBeTruthy();
    expect(screen.getByText("4 days")).toBeTruthy();
    expect(screen.getByText("Current streak")).toBeTruthy();
    expect(screen.getByText("First Session")).toBeTruthy();
    expect(screen.getByText("CanadaReady™ Scorecard")).toBeTruthy();
    expect(screen.getByLabelText("Open your CanadaReady Scorecard")).toBeTruthy();
    expect(screen.getByText("Skill radar")).toBeTruthy();
    expect(screen.getByText("Badges")).toBeTruthy();
    expect(screen.queryByText("sprout")).toBeNull();
  });

  it("keeps XP, streak, badges, and scorecard when the skill API fails", async () => {
    const screen = await renderProgress(new Error("offline"));
    expect(screen.queryByLabelText("Display Level 4")).toBeNull();
    expect(
      screen.getByText(
        "Skill levels need a connection — the rest of your progress is still here.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("240 XP total")).toBeTruthy();
    expect(screen.getByText("Current streak")).toBeTruthy();
    expect(screen.getByText("First Session")).toBeTruthy();
    expect(screen.getByText("CanadaReady™ Scorecard")).toBeTruthy();
    fireEvent.press(screen.getByLabelText("Retry loading skill levels"));
    await waitFor(() => {
      expect(mockFetchSkills).toHaveBeenCalledTimes(2);
    });
  });

  it("does not treat the XP level card as the four-skill Display Level", async () => {
    const screen = await renderProgress(criticalPayload());
    expect(screen.getByLabelText("Display Level 4")).toBeTruthy();
    expect(screen.getAllByText("Level 2").length).toBeGreaterThan(0);
    expect(screen.getByText("Level 4")).toBeTruthy();
  });
});
