/**
 * Tests for the Learn screen's handling of French scenarios (SN-020):
 * free French cards render unlocked and navigable, premium-gated French
 * cards show the lock overlay and open the SN-026 paywall instead of a
 * session, and a premium caller sees everything unlocked.
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
  CheckCircle2: () => null,
  ChevronRight: () => null,
  Lock: () => null,
  MessagesSquare: () => null,
  Target: () => null,
}));

// The real vector Icon fetches glyph fonts at render time and throws
// under react-test-renderer; only SN-035 tests render the pack rail.
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
      quest_date: "2026-08-24",
      timezone: "America/Toronto",
      quests: [],
    })),
  };
});

jest.mock("../../src/services/scenarioCache", () => ({
  loadScenarioCache: jest.fn(async () => null),
  saveScenarioCache: jest.fn(async () => undefined),
}));

// The Learn screen hydrates the Culture Corner progress store (SN-047),
// which reads AsyncStorage on mount.
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
        Object.keys(store).forEach((key) => delete store[key]);
      }),
    },
  };
});

import { fireEvent, render, waitFor } from "@testing-library/react-native";

import LearnScreen from "../../app/(tabs)/learn";
import { fetchPacks, fetchScenarios } from "../../src/api/client";
import { useAuthStore } from "../../src/stores/authStore";
import { useScenarioStore } from "../../src/stores/scenarioStore";

function makeUser(
  subscription_tier: string,
  preferred_language: "en" | "fr" = "fr",
) {
  return {
    id: "user-1",
    email: "pavan@example.com",
    name: "Pavan",
    native_language: "hi",
    target_language: "en-CA",
    learning_goal: "pr_readiness",
    current_level: "sprout",
    preferred_language,
    subscription_tier,
    streak_count: 0,
    streak_last_date: null,
    total_xp: 0,
    total_speaking_seconds: 0,
    onboarding_completed: true,
    created_at: "2026-08-24T12:00:00Z",
    skills: null,
  };
}

function frenchCatalog() {
  return [
    {
      id: "ramq-carte-sante-rendez-vous",
      title: "Prendre rendez-vous pour votre carte santé",
      description: "RAMQ",
      category: "healthcare",
      difficulty: 2,
      is_locked: false,
    },
    {
      id: "cegep-premium-fr",
      title: "Conseils cégep avancés",
      description: "Cégep",
      category: "education",
      difficulty: 3,
      is_locked: true,
    },
  ];
}

describe("Learn screen with French scenarios (SN-020)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  function seedStores(
    subscription_tier: string,
    scenarios: ReturnType<typeof frenchCatalog>,
  ) {
    useAuthStore.setState({
      user: makeUser(subscription_tier),
      token: "test-token",
      isLoading: false,
      isHydrated: true,
      isAuthenticated: true,
    });
    useScenarioStore.setState({
      scenarios,
      selected: scenarios[0],
      isLoading: false,
      error: null,
      isFromCache: false,
      // Matching language stops the mount effect from refetching.
      language: "fr",
    });
  }

  it("renders free French scenarios unlocked", async () => {
    seedStores("free", frenchCatalog());

    const screen = render(<LearnScreen />);

    await waitFor(() => {
      expect(
        screen.getByLabelText(
          "Start scenario: Prendre rendez-vous pour votre carte santé",
        ),
      ).toBeTruthy();
    });
  });

  it("locks premium French scenarios behind the paywall for free users", async () => {
    seedStores("free", frenchCatalog());

    const screen = render(<LearnScreen />);
    await waitFor(() => {
      expect(
        screen.getByLabelText("Premium scenario: Conseils cégep avancés"),
      ).toBeTruthy();
    });

    // The lock overlay marks the gated card...
    expect(screen.getByText("Premium")).toBeTruthy();
    // ...the catalog came straight from the store, no refetch...
    expect(fetchScenarios).not.toHaveBeenCalled();
    // ...and no session navigation happened implicitly.
    expect(mockRouter.push).not.toHaveBeenCalled();

    fireEvent.press(
      screen.getByLabelText("Premium scenario: Conseils cégep avancés"),
    );

    // Pressing opens the SN-026 paywall instead of a session route.
    await waitFor(() => {
      expect(screen.queryByLabelText("Sonolo premium upgrade")).toBeTruthy();
    });
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it("opens sessions directly for premium callers regardless of language", async () => {
    seedStores("premium", frenchCatalog());

    const screen = render(<LearnScreen />);

    await waitFor(() => {
      expect(
        screen.getByLabelText("Start scenario: Conseils cégep avancés"),
      ).toBeTruthy();
    });
    expect(screen.queryByText("Premium")).toBeNull();
  });
});

describe("Learn screen exact pack filtering (SN-035)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  const manifestPacks = [
    {
      id: "workplace-english-v1",
      type: "scenarios",
      title: "Workplace English",
      description: "Meetings, interviews, and workplace confidence.",
      category: "workplace",
      language: "en",
      tier: "freemium",
      theme_color: "#FF8A00",
      icon: "briefcase",
      scenario_count: 10,
      premium_count: 3,
    },
    {
      id: "healthcare-english-v1",
      type: "scenarios",
      title: "Healthcare English",
      description: "Doctors, pharmacies, and Canadian healthcare.",
      category: "healthcare",
      language: "en",
      tier: "freemium",
      theme_color: "#22C55E",
      icon: "briefcase",
      scenario_count: 10,
      premium_count: 3,
    },
  ];

  function seedEnglishStores(
    scenarios: Array<{
      id: string;
      title: string;
      description: string;
      category: string;
      target_language?: string;
      pack_id?: string;
      difficulty: number;
      is_locked: boolean;
    }>,
  ) {
    useAuthStore.setState({
      user: makeUser("free", "en"),
      token: "test-token",
      isLoading: false,
      isHydrated: true,
      isAuthenticated: true,
    });
    useScenarioStore.setState({
      scenarios,
      selected: scenarios[0],
      isLoading: false,
      error: null,
      isFromCache: false,
      language: "en",
    });
  }

  it("navigates to the pack detail screen when a pack card is tapped (SN-039)", async () => {
    (fetchPacks as jest.Mock).mockResolvedValueOnce(manifestPacks);
    seedEnglishStores([
      // Exact-id match even though the category disagrees with the pack.
      {
        id: "wp-mismatched-category",
        title: "Interview warm-up",
        description: "Work",
        category: "social",
        target_language: "en-CA",
        pack_id: "workplace-english-v1",
        difficulty: 2,
        is_locked: false,
      },
      // Cached before SN-035: no pack_id, so the category+language
      // fallback keeps it in the workplace view.
      {
        id: "legacy-no-pack-id",
        title: "Legacy workplace card",
        description: "Work",
        category: "workplace",
        target_language: "en-CA",
        difficulty: 2,
        is_locked: false,
      },
      // Same category + language as the pack but a different pack_id:
      // exactly the leak the old heuristic could not see.
      {
        id: "healthcare-in-workplace-clothes",
        title: "Bloodwork review",
        description: "Clinic",
        category: "workplace",
        target_language: "en-CA",
        pack_id: "healthcare-english-v1",
        difficulty: 3,
        is_locked: false,
      },
    ]);

    const screen = render(<LearnScreen />);
    await waitFor(() => {
      expect(
        screen.getByLabelText("Learning pack: Workplace English"),
      ).toBeTruthy();
    });

    fireEvent.press(screen.getByLabelText("Learning pack: Workplace English"));

    // SN-039: tapping a pack card routes to the Pack Detail screen
    // instead of filtering the library in place.
    expect(mockRouter.push).toHaveBeenCalledWith("/pack/workplace-english-v1");
    // The library below stays unfiltered — every scenario remains.
    expect(
      screen.getByLabelText("Start scenario: Interview warm-up"),
    ).toBeTruthy();
    expect(
      screen.getByLabelText("Start scenario: Legacy workplace card"),
    ).toBeTruthy();
    expect(
      screen.getByLabelText("Start scenario: Bloodwork review"),
    ).toBeTruthy();
  });

  it("uses the server scenario_count for the pack badge", async () => {
    (fetchPacks as jest.Mock).mockResolvedValueOnce(manifestPacks);
    seedEnglishStores([
      {
        id: "only-local-match",
        title: "Interview warm-up",
        description: "Work",
        category: "workplace",
        target_language: "en-CA",
        pack_id: "workplace-english-v1",
        difficulty: 2,
        is_locked: false,
      },
    ]);

    const screen = render(<LearnScreen />);
    await waitFor(() => {
      expect(
        screen.getByLabelText("Learning pack: Workplace English"),
      ).toBeTruthy();
    });

    // Server count (10) wins over recounting one local scenario; both
    // manifest packs carry scenario_count 10, hence getAllByText.
    expect(screen.getAllByText("10 scenarios").length).toBeGreaterThan(0);
    expect(screen.queryByText("1 scenario")).toBeNull();
  });
});
