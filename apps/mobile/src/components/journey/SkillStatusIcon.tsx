/**
 * C8 skill status glyph for a Journey Map unit row (§14.3).
 */
import { StyleSheet, Text, View } from "react-native";

import type { JourneySkillStatus } from "../../api/client";
import { colors } from "../../theme/colors";

const SKILL_EMOJI: Record<string, string> = {
  speaking: "🗣️",
  listening: "🎧",
  reading: "📖",
  writing: "✍️",
};

const STATUS_GLYPH: Record<JourneySkillStatus, string> = {
  complete: "✅",
  in_progress: "🔄",
  not_started: "⬜",
  locked: "🔒",
};

const STATUS_LABEL: Record<JourneySkillStatus, string> = {
  complete: "complete",
  in_progress: "in progress",
  not_started: "not started",
  locked: "locked",
};

export function skillStatusGlyph(status: JourneySkillStatus): string {
  return STATUS_GLYPH[status];
}

export function SkillStatusIcon({
  skill,
  status,
}: {
  skill: string;
  status: JourneySkillStatus;
}): JSX.Element {
  const name = skill.charAt(0).toUpperCase() + skill.slice(1);
  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={`${name} ${STATUS_LABEL[status]}`}
      style={styles.icon}
    >
      <Text style={styles.emoji}>{SKILL_EMOJI[skill] ?? ""}</Text>
      <Text style={styles.glyph}>{STATUS_GLYPH[status]}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  icon: {
    minWidth: 44,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
  },
  emoji: {
    fontSize: 14,
    color: colors.textPrimary,
  },
  glyph: {
    fontSize: 11,
    color: colors.textPrimary,
  },
});
