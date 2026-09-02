/**
 * C8 Journey Map: locked / active / completed bands, tap navigation,
 * and existing Learn rails preserved.
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
  BookOpen: () => null,
  CheckCircle2: () => null,
  ChevronRight: () => null,
  Lock: () => null,
  MessagesSquare: () => null,
  Target: () => null,
}));

jest.mock("@expo/vector-icons/Ionicons", () => ({
  __esModule: true,
  default: ({ name }: { name: string }) => {
    const { Text } = require("react-native");
    return <Text>{`ionicon:${name}`}</Text>;
  },
}));

jest.mock("../../src/components/PaywallModal", () => {
  const { Text } = require("react-native");
  return {
    PaywallModal: ({ visible }: { visible: boolean }) =>
      visible ? (
        <Text accessibilityLabel="Sonolo premium upgrade">paywall open</Text>
      ) : null,
  };
});

jest.mock("../../src/api/client", () => {
  const actual = jest.requireActual("../../src/api/client");
  return {
    ...actual,
    fetchScenarios: jest.fn(async () => []),
    fetchPacks: jest.fn(async () => []),
    fetchMicrolessons: jest.fn(async () => []),
    fetchPronunciationDrills: jest.fn(async () => []),
    fetchListeningDialogues: jest.fn(async () => []),
    fetchJourney: jest.fn(async () => {
      throw new Error("offline");
    }),
    fetchTodayQuests: jest.fn(async () => ({
      quest_date: "2026-09-02",
      timezone: "America/Toronto",
      quests: [],
    })),
  };
});

jest.mock("../../src/services/scenarioCache", () => ({
  loadScenarioCache: jest.fn(async () => null),
  saveScenarioCache: jest.fn(async () => undefined),
}));

jest.mock("@react-native-async-storage/async-storage", () => {
  const store: Record<string, string> = {};
  return {
    __esModule: true,
    default: {
      getItem: jest.fn(async (key: string) => store[key] ?? null),
      setItem: jest.fn(async (key: string, value: string) => {
        store[key] = value;
      }),
      removeItem: jest.fn(async (key: string) => {
        delete store[key];
      }),
      clear: jest.fn(async () => {
        Object.keys(store).forEach((key) => {
          delete store[key];
        });
      }),
    },
  };
});

import { fireEvent, render, waitFor } from "@testing-library/react-native";

import LearnScreen from "../../app/(tabs)/learn";
import {
  fetchJourney,
  fetchListeningDialogues,
  fetchMicrolessons,
  fetchPacks,
  fetchPronunciationDrills,
  fetchTodayQuests,
  type JourneyBand,
  type JourneyMapData,
  type JourneySkillStatus,
  type JourneyUnit,
  type JourneyUnitStatus,
} from "../../src/api/client";
import { JourneyMap } from "../../src/components/journey/JourneyMap";
import { SkillStatusIcon } from "../../src/components/journey/SkillStatusIcon";
import { UnitRow } from "../../src/components/journey/UnitRow";
import { useAuthStore } from "../../src/stores/authStore";
import { useScenarioStore } from "../../src/stores/scenarioStore";

const SKILLS = ["speaking", "listening", "reading", "writing"] as const;

const TITLES: Record<string, string> = {
  F1: "Arrival Day",
  F2: "Finding Home",
  F3: "First Week",
  F4: "Getting Help",
  F5: "Money Matters",
  F6: "Community",
  M1: "First Job",
  M2: "Workplace Life",
  M3: "Government & Services",
  M4: "Social Confidence",
  M5: "Canadian Culture",
  M6: "Health & Safety",
  A1: "PR Readiness",
  A2: "Professional Growth",
  A3: "Complex Situations",
  A4: "Academic English",
  A5: "Media & Persuasion",
  A6: "Life in Canada Mastery",
};

const BAND_UNITS: Record<string, string[]> = {
  advanced: ["A1", "A2", "A3", "A4", "A5", "A6"],
  middle: ["M1", "M2", "M3", "M4", "M5", "M6"],
  foundation: ["F1", "F2", "F3", "F4", "F5", "F6"],
};

function skillList(status: JourneySkillStatus) {
  return SKILLS.map((skill) => ({ skill, status }));
}

function makeUnit(
  id: string,
  status: JourneyUnitStatus,
  skillStatus: JourneySkillStatus,
): JourneyUnit {
  return {
    id,
    title: TITLES[id],
    status,
    skills: skillList(skillStatus),
  };
}

function makeBand(
  id: "advanced" | "middle" | "foundation",
  status: JourneyBand["status"],
  units: JourneyUnit[],
): JourneyBand {
  const copy = {
    advanced: {
      title: "Advanced Band",
      subtitle: "Speaking with Power",
      icon: "🌲",
      unlock: "Complete Middle Band to unlock",
    },
    middle: {
      title: "Middle Band",
      subtitle: "Finding Your Voice",
      icon: "🌿",
      unlock: "Complete Foundation Band to unlock",
    },
    foundation: {
      title: "Foundation Band",
      subtitle: "First Steps",
      icon: "🌱",
      unlock: "Complete the previous band to unlock",
    },
  }[id];
  return {
    id,
    title: copy.title,
    subtitle: copy.subtitle,
    icon: copy.icon,
    status,
    expanded: status === "active",
    unlock_condition: status === "locked" ? copy.unlock : null,
    units,
  };
}

function allLockedJourney(): JourneyMapData {
  return {
    current_unit_id: null,
    bands: (["advanced", "middle", "foundation"] as const).map((bandId) =>
      makeBand(
        bandId,
        "locked",
        BAND_UNITS[bandId].map((code) => makeUnit(code, "locked", "locked")),
      ),
    ),
  };
}

/** Shape of GET /api/learn/journey for a user with no unit progress. */
function realisticFreshJourney(): JourneyMapData {
  return {
    current_unit_id: "F1",
    bands: [
      makeBand(
        "advanced",
        "locked",
        BAND_UNITS.advanced.map((code) => makeUnit(code, "locked", "locked")),
      ),
      makeBand(
        "middle",
        "locked",
        BAND_UNITS.middle.map((code) => makeUnit(code, "locked", "locked")),
      ),
      makeBand("foundation", "active", [
        makeUnit("F1", "current", "in_progress"),
        ...BAND_UNITS.foundation
          .slice(1)
          .map((code) => makeUnit(code, "locked", "locked")),
      ]),
    ],
  };
}

function partialJourney(): JourneyMapData {
  return {
    current_unit_id: "F2",
    bands: [
      makeBand(
        "advanced",
        "locked",
        BAND_UNITS.advanced.map((code) => makeUnit(code, "locked", "locked")),
      ),
      makeBand(
        "middle",
        "locked",
        BAND_UNITS.middle.map((code) => makeUnit(code, "locked", "locked")),
      ),
      makeBand("foundation", "active", [
        {
          id: "F1",
          title: "Arrival Day",
          status: "completed",
          skills: skillList("complete"),
        },
        makeUnit("F2", "current", "in_progress"),
        ...BAND_UNITS.foundation
          .slice(2)
          .map((code) => makeUnit(code, "locked", "locked")),
      ]),
    ],
  };
}

function foundationCompleteJourney(): JourneyMapData {
  return {
    current_unit_id: "M1",
    bands: [
      makeBand(
        "advanced",
        "locked",
        BAND_UNITS.advanced.map((code) => makeUnit(code, "locked", "locked")),
      ),
      makeBand("middle", "active", [
        makeUnit("M1", "current", "in_progress"),
        ...BAND_UNITS.middle
          .slice(1)
          .map((code) => makeUnit(code, "locked", "locked")),
      ]),
      makeBand(
        "foundation",
        "completed",
        BAND_UNITS.foundation.map((code) =>
          makeUnit(code, "completed", "complete"),
        ),
      ),
    ],
  };
}

function fullyCompleteJourney(): JourneyMapData {
  return {
    current_unit_id: null,
    bands: (["advanced", "middle", "foundation"] as const).map((bandId) =>
      makeBand(
        bandId,
        "completed",
        BAND_UNITS[bandId].map((code) =>
          makeUnit(code, "completed", "complete"),
        ),
      ),
    ),
  };
}

function makeUser() {
  return {
    id: "user-1",
    email: "pavan@example.com",
    name: "Pavan",
    native_language: "hi",
    target_language: "en-CA",
    learning_goal: "pr_readiness",
    current_level: "sprout",
    preferred_language: "en" as const,
    subscription_tier: "free",
    streak_count: 0,
    streak_last_date: null,
    total_xp: 0,
    total_speaking_seconds: 0,
    onboarding_completed: true,
    created_at: "2026-09-02T12:00:00Z",
    skills: null,
  };
}

describe("JourneyMap component states (C8)", () => {
  it("renders all bands and units locked", () => {
    const onUnitPress = jest.fn();
    const screen = render(
      <JourneyMap journey={allLockedJourney()} onUnitPress={onUnitPress} />,
    );
    expect(screen.getByTestId("journey-map")).toBeTruthy();
    expect(screen.getByText("Your Journey")).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Advanced Band, locked. Complete Middle Band to unlock",
      ),
    ).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Middle Band, locked. Complete Foundation Band to unlock",
      ),
    ).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Foundation Band, locked. Complete the previous band to unlock",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Complete Middle Band to unlock")).toBeTruthy();
    expect(screen.getByText("Complete Foundation Band to unlock")).toBeTruthy();
    expect(screen.queryByLabelText("Current unit: F1: Arrival Day")).toBeNull();
    expect(screen.queryByText("📍 Current")).toBeNull();
  });

  it("expands the active band and keeps completed bands collapsed", () => {
    const screen = render(
      <JourneyMap
        journey={foundationCompleteJourney()}
        onUnitPress={jest.fn()}
      />,
    );
    expect(screen.getByLabelText("Middle Band, active")).toBeTruthy();
    expect(screen.getByLabelText("Current unit: M1: First Job")).toBeTruthy();
    expect(screen.getByText("📍 Current")).toBeTruthy();
    expect(screen.getByLabelText("Foundation Band, complete")).toBeTruthy();
    expect(screen.getByText("✅ Complete")).toBeTruthy();
    expect(
      screen.queryByLabelText("Completed unit: F1: Arrival Day"),
    ).toBeNull();
    expect(
      screen.getByLabelText(
        "Advanced Band, locked. Complete Middle Band to unlock",
      ),
    ).toBeTruthy();
  });

  it("shows locked later units grayed inside the active band", () => {
    const screen = render(
      <JourneyMap journey={realisticFreshJourney()} onUnitPress={jest.fn()} />,
    );
    expect(screen.getByLabelText("Foundation Band, active")).toBeTruthy();
    expect(screen.getByLabelText("Current unit: F1: Arrival Day")).toBeTruthy();
    expect(screen.getByLabelText("Locked unit: F2: Finding Home")).toBeTruthy();
    expect(screen.getByLabelText("Locked unit: F3: First Week")).toBeTruthy();
    expect(screen.queryByLabelText("Locked unit: M1: First Job")).toBeNull();
  });

  it("collapses every band when the curriculum is fully complete", () => {
    const screen = render(
      <JourneyMap journey={fullyCompleteJourney()} onUnitPress={jest.fn()} />,
    );
    expect(screen.getAllByText("✅ Complete")).toHaveLength(3);
    expect(screen.queryByText("📍 Current")).toBeNull();
    expect(screen.queryByLabelText("Current unit: F1: Arrival Day")).toBeNull();
    expect(
      screen.queryByLabelText("Completed unit: A6: Life in Canada Mastery"),
    ).toBeNull();
  });

  it("expands a completed band on header tap so units can be reviewed", () => {
    const screen = render(
      <JourneyMap
        journey={foundationCompleteJourney()}
        onUnitPress={jest.fn()}
      />,
    );
    fireEvent.press(screen.getByLabelText("Foundation Band, complete"));
    expect(
      screen.getByLabelText("Completed unit: F1: Arrival Day"),
    ).toBeTruthy();
    expect(
      screen.getByLabelText("Completed unit: F6: Community"),
    ).toBeTruthy();
  });

  it("does not expand a locked band on header tap", () => {
    const screen = render(
      <JourneyMap journey={realisticFreshJourney()} onUnitPress={jest.fn()} />,
    );
    fireEvent.press(
      screen.getByLabelText(
        "Advanced Band, locked. Complete Middle Band to unlock",
      ),
    );
    expect(screen.queryByLabelText("Locked unit: A1: PR Readiness")).toBeNull();
  });

  it("navigates on unlocked unit tap and ignores locked units", () => {
    const onUnitPress = jest.fn();
    const screen = render(
      <JourneyMap journey={partialJourney()} onUnitPress={onUnitPress} />,
    );
    fireEvent.press(screen.getByLabelText("Current unit: F2: Finding Home"));
    expect(onUnitPress).toHaveBeenCalledTimes(1);
    expect(onUnitPress.mock.calls[0][0].id).toBe("F2");
    fireEvent.press(screen.getByLabelText("Locked unit: F3: First Week"));
    expect(onUnitPress).toHaveBeenCalledTimes(1);
    fireEvent.press(screen.getByLabelText("Completed unit: F1: Arrival Day"));
    expect(onUnitPress).toHaveBeenCalledTimes(2);
    expect(onUnitPress.mock.calls[1][0].id).toBe("F1");
  });
});

describe("SkillStatusIcon and UnitRow (C8)", () => {
  it("renders the four status glyphs", () => {
    const complete = render(
      <SkillStatusIcon skill="speaking" status="complete" />,
    );
    expect(complete.getByLabelText("Speaking complete")).toBeTruthy();
    const progress = render(
      <SkillStatusIcon skill="listening" status="in_progress" />,
    );
    expect(progress.getByLabelText("Listening in progress")).toBeTruthy();
    const idle = render(
      <SkillStatusIcon skill="reading" status="not_started" />,
    );
    expect(idle.getByLabelText("Reading not started")).toBeTruthy();
    const locked = render(
      <SkillStatusIcon skill="writing" status="locked" />,
    );
    expect(locked.getByLabelText("Writing locked")).toBeTruthy();
  });

  it("does not fire onPress for a locked unit row", () => {
    const onPress = jest.fn();
    const unit = makeUnit("F4", "locked", "locked");
    const screen = render(<UnitRow unit={unit} onPress={onPress} />);
    fireEvent.press(screen.getByLabelText("Locked unit: F4: Getting Help"));
    expect(onPress).not.toHaveBeenCalled();
  });
});

describe("Learn tab Journey Map + existing rails (C8)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useAuthStore.setState({
      user: makeUser(),
      token: "test-token",
      isLoading: false,
      isHydrated: true,
      isAuthenticated: true,
    });
    useScenarioStore.setState({
      scenarios: [
        {
          id: "airport-taxi",
          title: "Airport taxi",
          description: "Landed",
          category: "travel",
          difficulty: 2,
          is_locked: false,
        },
      ],
      selected: null,
      isLoading: false,
      error: null,
      isFromCache: false,
      language: "en",
    });
    (fetchJourney as jest.Mock).mockResolvedValue(realisticFreshJourney());
    (fetchPacks as jest.Mock).mockResolvedValue([
      {
        id: "workplace-english-v1",
        type: "scenarios",
        title: "Workplace English",
        description: "Meetings",
        category: "workplace",
        language: "en",
        tier: "freemium",
        theme_color: "#FF8A00",
        icon: "briefcase",
        scenario_count: 10,
        premium_count: 3,
      },
    ]);
    (fetchMicrolessons as jest.Mock).mockResolvedValue([
      {
        id: "tipping",
        title: "Tipping in Canada",
        hook: "Who to tip",
        read_minutes: 1,
        theme_color: "#E11D48",
        icon: "🍁",
      },
    ]);
    (fetchPronunciationDrills as jest.Mock).mockResolvedValue([
      {
        id: "th-sound",
        title: "The TH sound",
        focus: "th",
        level: "A1",
        is_premium: false,
        is_locked: false,
        theme_color: "#A78BFA",
        icon: "🗣️",
      },
    ]);
    (fetchListeningDialogues as jest.Mock).mockResolvedValue([
      {
        id: "store-chat",
        title: "At the Superstore",
        listening_focus: "aisle numbers",
        is_locked: false,
        theme_color: "#06B6D4",
        icon: "🎧",
      },
    ]);
    (fetchTodayQuests as jest.Mock).mockResolvedValue({
      quest_date: "2026-09-02",
      timezone: "America/Toronto",
      quests: [
        {
          code: "session_1",
          title: "Complete a session",
          description: "Speak once today",
          target_count: 1,
          progress_count: 0,
          reward_xp: 20,
          completed: false,
        },
      ],
    });
  });

  it("renders the journey from the backend payload above existing rails", async () => {
    const screen = render(<LearnScreen />);
    await waitFor(() => {
      expect(screen.getByTestId("journey-map")).toBeTruthy();
    });
    expect(screen.getByLabelText("Foundation Band, active")).toBeTruthy();
    expect(screen.getByLabelText("Current unit: F1: Arrival Day")).toBeTruthy();
    expect(screen.getByLabelText("Locked unit: F3: First Week")).toBeTruthy();
    expect(screen.getByText("Culture Corner")).toBeTruthy();
    expect(screen.getByText("Pronunciation Lab")).toBeTruthy();
    expect(screen.getByText("Listening Gym")).toBeTruthy();
    expect(screen.getByText("Today's quests")).toBeTruthy();
    expect(screen.getByText("Scenario library")).toBeTruthy();
    expect(screen.getByLabelText("Learning pack: Workplace English")).toBeTruthy();
    expect(screen.getByLabelText("Culture lesson: Tipping in Canada")).toBeTruthy();
    expect(
      screen.getByLabelText("Pronunciation drill: The TH sound"),
    ).toBeTruthy();
    expect(
      screen.getByLabelText("Listening dialogue: At the Superstore"),
    ).toBeTruthy();
    expect(screen.getByLabelText("Start scenario: Airport taxi")).toBeTruthy();
  });

  it("navigates to /unit/:id from the Learn tab current unit", async () => {
    const screen = render(<LearnScreen />);
    await waitFor(() => {
      expect(screen.getByLabelText("Current unit: F1: Arrival Day")).toBeTruthy();
    });
    fireEvent.press(screen.getByLabelText("Current unit: F1: Arrival Day"));
    expect(mockRouter.push).toHaveBeenCalledWith("/unit/F1");
    fireEvent.press(screen.getByLabelText("Locked unit: F2: Finding Home"));
    expect(mockRouter.push).toHaveBeenCalledTimes(1);
  });

  it("keeps the scenario library usable when the journey request fails", async () => {
    (fetchJourney as jest.Mock).mockRejectedValueOnce(new Error("offline"));
    const screen = render(<LearnScreen />);
    await waitFor(() => {
      expect(screen.getByLabelText("Start scenario: Airport taxi")).toBeTruthy();
    });
    expect(screen.queryByTestId("journey-map")).toBeNull();
    expect(screen.getByText("Scenario library")).toBeTruthy();
  });
});
