/**
 * C10 activity sequence for a unit. Completion comes from C8 journey
 * skill flags (user_unit_progress). Skill blocks are independently
 * available. Unit Test unlocks only when all four skill flags are
 * complete — not from content IDs existing.
 */
import type {
  JourneyMapData,
  JourneySkillStatus,
  JourneyUnit,
  UnitDetail,
} from "../../api/client";

export type UnitActivityKey =
  | "vocab"
  | "listening"
  | "reading"
  | "speaking"
  | "writing"
  | "grammar"
  | "review"
  | "unit_test";

export type UnitActivityState = "completed" | "available" | "locked";

export interface UnitActivity {
  key: UnitActivityKey;
  title: string;
  subtitle: string;
  skill: "speaking" | "listening" | "reading" | "writing" | null;
  state: UnitActivityState;
  route: string | null;
}

const SKILLS = ["speaking", "listening", "reading", "writing"] as const;

export function findJourneyUnit(
  journey: JourneyMapData | null,
  unitCode: string,
): JourneyUnit | null {
  if (journey === null) {
    return null;
  }
  for (const band of journey.bands) {
    const match = band.units.find((unit) => unit.id === unitCode);
    if (match !== undefined) {
      return match;
    }
  }
  return null;
}

export function skillFlag(
  journeyUnit: JourneyUnit | null,
  skill: (typeof SKILLS)[number],
): JourneySkillStatus | "missing" {
  if (journeyUnit === null) {
    return "missing";
  }
  const row = journeyUnit.skills.find((item) => item.skill === skill);
  return row?.status ?? "missing";
}

export function allSkillBlocksComplete(journeyUnit: JourneyUnit | null): boolean {
  return SKILLS.every((skill) => skillFlag(journeyUnit, skill) === "complete");
}

function skillBlockState(
  journeyUnit: JourneyUnit | null,
  skill: (typeof SKILLS)[number],
): UnitActivityState {
  if (journeyUnit?.status === "locked") {
    return "locked";
  }
  if (skillFlag(journeyUnit, skill) === "complete") {
    return "completed";
  }
  return "available";
}

function supportBlockState(journeyUnit: JourneyUnit | null): UnitActivityState {
  if (journeyUnit?.status === "locked") {
    return "locked";
  }
  return "available";
}

function unitTestState(journeyUnit: JourneyUnit | null): UnitActivityState {
  if (journeyUnit?.status === "completed") {
    return "completed";
  }
  if (journeyUnit?.status === "locked") {
    return "locked";
  }
  if (allSkillBlocksComplete(journeyUnit)) {
    return "available";
  }
  return "locked";
}

export function buildUnitActivities(
  unit: UnitDetail | null,
  journeyUnit: JourneyUnit | null,
): UnitActivity[] {
  const vocabCount = unit?.vocabulary_targets.length ?? 0;
  const grammar = unit?.grammar_targets[0];
  const listeningId = unit?.listening_ids[0];
  const speakingId = unit?.speaking_ids[0];

  return [
    {
      key: "vocab",
      title: "Vocabulary Primer",
      subtitle:
        vocabCount > 0
          ? `${vocabCount} key words · 5 min`
          : "Flashcards · 5 min",
      skill: null,
      state: supportBlockState(journeyUnit),
      route: null,
    },
    {
      key: "listening",
      title: "Listening — TuneIn",
      subtitle: "Audio story, quiz, and dictation · 8 min",
      skill: "listening",
      state: skillBlockState(journeyUnit, "listening"),
      route: listeningId ? `/listening/${listeningId}` : null,
    },
    {
      key: "reading",
      title: "Reading — ReadOn",
      subtitle: "Text, questions, and vocabulary hunt · 8 min",
      skill: "reading",
      state: skillBlockState(journeyUnit, "reading"),
      route: null,
    },
    {
      key: "speaking",
      title: "Speaking — SpeakUp",
      subtitle: "Pronunciation, conversation, SpeakSprint · 10 min",
      skill: "speaking",
      state: skillBlockState(journeyUnit, "speaking"),
      route: speakingId ? `/session/${speakingId}` : null,
    },
    {
      key: "writing",
      title: "Writing — WriteRight",
      subtitle: "Sentence builder, guided write, error fix · 8 min",
      skill: "writing",
      state: skillBlockState(journeyUnit, "writing"),
      route: null,
    },
    {
      key: "grammar",
      title: "Grammar Spotlight",
      subtitle: grammar ?? "Mini-lesson · 3 min",
      skill: null,
      state: supportBlockState(journeyUnit),
      route: null,
    },
    {
      key: "review",
      title: "Review & Reinforce",
      subtitle: "FSRS vocabulary review · 3 min",
      skill: null,
      state: supportBlockState(journeyUnit),
      route: null,
    },
    {
      key: "unit_test",
      title: "Unit Test",
      subtitle: "Pass ≥70% overall and ≥60% per skill · 15 min",
      skill: null,
      state: unitTestState(journeyUnit),
      route: null,
    },
  ];
}

export function activityAccessibilityLabel(activity: UnitActivity): string {
  if (activity.state === "locked") {
    if (activity.key === "unit_test") {
      return "Unit Test, locked. Complete all skill blocks to unlock";
    }
    return `${activity.title}, locked`;
  }
  if (activity.state === "completed") {
    return `${activity.title}, complete`;
  }
  return `${activity.title}, available`;
}
