/**
 * C10 Unit Detail: §15.3 F3 sequence, independent skill-block unlock,
 * Unit Test gated on four skill completions (not content IDs).
 */

const mockRouter = {
  replace: jest.fn(),
  push: jest.fn(),
  navigate: jest.fn(),
  back: jest.fn(),
};

const mockParams = { id: "F3" };

jest.mock("expo-router", () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => mockParams,
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

jest.mock("lucide-react-native", () => ({
  CheckCircle2: () => null,
  ChevronLeft: () => null,
  ChevronRight: () => null,
  Lock: () => null,
}));

jest.mock("../../src/api/client", () => {
  const actual = jest.requireActual("../../src/api/client");
  return {
    ...actual,
    fetchUnit: jest.fn(),
    fetchJourney: jest.fn(),
  };
});

import { fireEvent, render, waitFor } from "@testing-library/react-native";

import UnitDetailScreen from "../../app/unit/[id]";
import {
  fetchJourney,
  fetchUnit,
  type JourneyMapData,
  type JourneySkillStatus,
  type UnitDetail,
} from "../../src/api/client";
import {
  allSkillBlocksComplete,
  buildUnitActivities,
  findJourneyUnit,
} from "../../src/components/unit/unitActivities";

const mockFetchUnit = fetchUnit as jest.Mock;
const mockFetchJourney = fetchJourney as jest.Mock;

function f3Catalog(): UnitDetail {
  return {
    id: "F3",
    band: "foundation",
    title: "First Week",
    story_chapter: "Grocery run & transit",
    theme: "daily_errands",
    icon: "🛒",
    level_target: 2,
    language: "en-CA",
    vocabulary_targets: Array.from({ length: 20 }, (_, index) => `word-${index}`),
    grammar_targets: ["Articles: a, an, the"],
    reading_ids: ["reading-F3-grocery-flyer"],
    writing_ids: [
      "writing-F3-sentence-builder",
      "writing-F3-shopping-list",
      "writing-F3-error-fix",
    ],
    listening_ids: ["listen-F3-superstore"],
    speaking_ids: [],
    reading_required_activities: [
      { id: "reading-F3-grocery-flyer", type: "reading_exercise" },
      { id: "hunt-F3-grocery-flyer", type: "vocabulary_hunt" },
    ],
    reading_optional_activities: [],
    unit_test_id: "test-F3",
    is_published: true,
  };
}

function skills(
  status: Record<string, JourneySkillStatus>,
): Array<{ skill: string; status: JourneySkillStatus }> {
  return ["speaking", "listening", "reading", "writing"].map((skill) => ({
    skill,
    status: status[skill] ?? "not_started",
  }));
}

function journeyWithF3(
  status: "current" | "completed" | "locked",
  skillStatus: Record<string, JourneySkillStatus>,
): JourneyMapData {
  return {
    current_unit_id: status === "current" ? "F3" : null,
    bands: [
      {
        id: "advanced",
        title: "Advanced Band",
        subtitle: "Speaking with Power",
        icon: "🌲",
        status: "locked",
        expanded: false,
        unlock_condition: "Complete Middle Band to unlock",
        units: [],
      },
      {
        id: "middle",
        title: "Middle Band",
        subtitle: "Finding Your Voice",
        icon: "🌿",
        status: "locked",
        expanded: false,
        unlock_condition: "Complete Foundation Band to unlock",
        units: [],
      },
      {
        id: "foundation",
        title: "Foundation Band",
        subtitle: "First Steps",
        icon: "🌱",
        status: "active",
        expanded: true,
        unlock_condition: null,
        units: [
          {
            id: "F3",
            title: "First Week",
            status,
            skills: skills(skillStatus),
          },
        ],
      },
    ],
  };
}

const incompleteSkills = {
  speaking: "not_started" as const,
  listening: "in_progress" as const,
  reading: "not_started" as const,
  writing: "not_started" as const,
};

const partialSkills = {
  speaking: "not_started" as const,
  listening: "complete" as const,
  reading: "complete" as const,
  writing: "in_progress" as const,
};

const allCompleteSkills = {
  speaking: "complete" as const,
  listening: "complete" as const,
  reading: "complete" as const,
  writing: "complete" as const,
};

async function renderUnit(journey: JourneyMapData, catalog: UnitDetail | Error) {
  mockParams.id = "F3";
  if (catalog instanceof Error) {
    mockFetchUnit.mockRejectedValue(catalog);
  } else {
    mockFetchUnit.mockResolvedValue(catalog);
  }
  mockFetchJourney.mockResolvedValue(journey);
  const screen = render(<UnitDetailScreen />);
  await waitFor(() => {
    expect(screen.getByLabelText("Unit F3: First Week")).toBeTruthy();
  });
  return screen;
}

describe("buildUnitActivities lock rules (C10)", () => {
  const catalog = f3Catalog();

  it("keeps skill blocks independently available when others are incomplete", () => {
    const journeyUnit = findJourneyUnit(
      journeyWithF3("current", partialSkills),
      "F3",
    );
    const rows = buildUnitActivities(catalog, journeyUnit);
    const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));
    expect(byKey.listening.state).toBe("completed");
    expect(byKey.reading.state).toBe("completed");
    expect(byKey.speaking.state).toBe("available");
    expect(byKey.writing.state).toBe("available");
    expect(byKey.vocab.state).toBe("available");
    expect(byKey.grammar.state).toBe("available");
    expect(byKey.review.state).toBe("available");
    expect(byKey.unit_test.state).toBe("locked");
    expect(allSkillBlocksComplete(journeyUnit)).toBe(false);
  });

  it("does not unlock the Unit Test just because content IDs exist", () => {
    const journeyUnit = findJourneyUnit(
      journeyWithF3("current", incompleteSkills),
      "F3",
    );
    const rows = buildUnitActivities(catalog, journeyUnit);
    expect(catalog.listening_ids).toHaveLength(1);
    expect(catalog.unit_test_id).toBe("test-F3");
    expect(rows.find((row) => row.key === "unit_test")?.state).toBe("locked");
  });

  it("unlocks the Unit Test only when all four skill flags are complete", () => {
    const locked = buildUnitActivities(
      catalog,
      findJourneyUnit(journeyWithF3("current", partialSkills), "F3"),
    );
    expect(locked.find((row) => row.key === "unit_test")?.state).toBe("locked");
    const open = buildUnitActivities(
      catalog,
      findJourneyUnit(journeyWithF3("current", allCompleteSkills), "F3"),
    );
    expect(open.find((row) => row.key === "unit_test")?.state).toBe("available");
  });

  it("marks the Unit Test complete after unit_test_passed (journey completed)", () => {
    const rows = buildUnitActivities(
      catalog,
      findJourneyUnit(journeyWithF3("completed", allCompleteSkills), "F3"),
    );
    expect(rows.find((row) => row.key === "unit_test")?.state).toBe("completed");
  });

  it("routes listening to the existing gym player and does not invent reading/writing routes", () => {
    const rows = buildUnitActivities(catalog, null);
    expect(rows.find((row) => row.key === "listening")?.route).toBe(
      "/listening/listen-F3-superstore",
    );
    expect(rows.find((row) => row.key === "reading")?.route).toBeNull();
    expect(rows.find((row) => row.key === "writing")?.route).toBeNull();
    expect(rows.find((row) => row.key === "unit_test")?.route).toBeNull();
    expect(rows.find((row) => row.key === "speaking")?.route).toBeNull();
  });
});

describe("Unit Detail screen (C10)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockParams.id = "F3";
  });

  it("renders the F3 header and every §15.3 activity block", async () => {
    const screen = await renderUnit(
      journeyWithF3("current", incompleteSkills),
      f3Catalog(),
    );
    expect(screen.getByText(/F3: First Week/)).toBeTruthy();
    expect(screen.getByText("Foundation Band · Level 2")).toBeTruthy();
    expect(screen.getByText('Story: “Grocery run & transit”')).toBeTruthy();
    expect(screen.getByLabelText("Vocabulary Primer, available")).toBeTruthy();
    expect(screen.getByLabelText("Listening — TuneIn, available")).toBeTruthy();
    expect(screen.getByLabelText("Reading — ReadOn, available")).toBeTruthy();
    expect(screen.getByLabelText("Speaking — SpeakUp, available")).toBeTruthy();
    expect(screen.getByLabelText("Writing — WriteRight, available")).toBeTruthy();
    expect(screen.getByLabelText("Grammar Spotlight, available")).toBeTruthy();
    expect(screen.getByLabelText("Review & Reinforce, available")).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Unit Test, locked. Complete all skill blocks to unlock",
      ),
    ).toBeTruthy();
  });

  it("shows completed skill rows and keeps Unit Test locked while any skill is incomplete", async () => {
    const screen = await renderUnit(
      journeyWithF3("current", partialSkills),
      f3Catalog(),
    );
    expect(screen.getByLabelText("Listening — TuneIn, complete")).toBeTruthy();
    expect(screen.getByLabelText("Reading — ReadOn, complete")).toBeTruthy();
    expect(screen.getByLabelText("Speaking — SpeakUp, available")).toBeTruthy();
    expect(screen.getByLabelText("Writing — WriteRight, available")).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Unit Test, locked. Complete all skill blocks to unlock",
      ),
    ).toBeTruthy();
  });

  it("unlocks Unit Test only after all four skill blocks are complete", async () => {
    const screen = await renderUnit(
      journeyWithF3("current", allCompleteSkills),
      f3Catalog(),
    );
    expect(screen.getByLabelText("Unit Test, available")).toBeTruthy();
    expect(screen.getByLabelText("Listening — TuneIn, complete")).toBeTruthy();
    expect(screen.getByLabelText("Speaking — SpeakUp, complete")).toBeTruthy();
  });

  it("navigates listening to the existing route and ignores locked Unit Test taps", async () => {
    const screen = await renderUnit(
      journeyWithF3("current", incompleteSkills),
      f3Catalog(),
    );
    fireEvent.press(screen.getByLabelText("Listening — TuneIn, available"));
    expect(mockRouter.push).toHaveBeenCalledWith(
      "/listening/listen-F3-superstore",
    );
    fireEvent.press(
      screen.getByLabelText(
        "Unit Test, locked. Complete all skill blocks to unlock",
      ),
    );
    expect(mockRouter.push).toHaveBeenCalledTimes(1);
  });

  it("does not invent a reading or writing route when those screens do not exist", async () => {
    const screen = await renderUnit(
      journeyWithF3("current", incompleteSkills),
      f3Catalog(),
    );
    fireEvent.press(screen.getByLabelText("Reading — ReadOn, available"));
    fireEvent.press(screen.getByLabelText("Writing — WriteRight, available"));
    expect(mockRouter.push).not.toHaveBeenCalled();
  });

  it("shows the unit-complete banner when the journey unit is completed", async () => {
    const screen = await renderUnit(
      journeyWithF3("completed", allCompleteSkills),
      f3Catalog(),
    );
    expect(screen.getByText("✅ Unit complete")).toBeTruthy();
    expect(screen.getByLabelText("Unit Test, complete")).toBeTruthy();
  });

  it("still renders the sequence from journey data when the catalog 404s", async () => {
    mockParams.id = "F3";
    mockFetchUnit.mockRejectedValue(new Error("404"));
    mockFetchJourney.mockResolvedValue(
      journeyWithF3("current", incompleteSkills),
    );
    const screen = render(<UnitDetailScreen />);
    await waitFor(() => {
      expect(screen.getByLabelText("Unit F3: First Week")).toBeTruthy();
    });
    expect(screen.getByLabelText("Vocabulary Primer, available")).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Unit Test, locked. Complete all skill blocks to unlock",
      ),
    ).toBeTruthy();
  });
});
