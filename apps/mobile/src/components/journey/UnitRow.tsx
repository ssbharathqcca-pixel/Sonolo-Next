/**
 * C8 unit row inside an expanded band: title, four skill icons, tap to enter.
 */
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { JourneyUnit } from "../../api/client";
import { colors } from "../../theme/colors";
import { SkillStatusIcon } from "./SkillStatusIcon";

export function unitRowLabel(unit: JourneyUnit): string {
  const title = `${unit.id}: ${unit.title}`;
  if (unit.status === "locked") {
    return `Locked unit: ${title}`;
  }
  if (unit.status === "current") {
    return `Current unit: ${title}`;
  }
  if (unit.status === "completed") {
    return `Completed unit: ${title}`;
  }
  return title;
}

export function UnitRow({
  unit,
  onPress,
}: {
  unit: JourneyUnit;
  onPress: (unit: JourneyUnit) => void;
}): JSX.Element {
  const locked = unit.status === "locked";
  const current = unit.status === "current";

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={unitRowLabel(unit)}
      accessibilityState={{ disabled: locked }}
      disabled={locked}
      onPress={() => {
        if (!locked) {
          onPress(unit);
        }
      }}
      style={({ pressed }) => [
        styles.row,
        current && styles.current,
        locked && styles.locked,
        pressed && !locked && styles.pressed,
      ]}
    >
      <View style={styles.titleBlock}>
        <Text style={styles.title} numberOfLines={1}>
          {unit.id}: {unit.title}
        </Text>
        {current ? <Text style={styles.currentBadge}>📍 Current</Text> : null}
        {unit.status === "completed" ? (
          <Text style={styles.completeHint}>Complete</Text>
        ) : null}
      </View>
      <View style={styles.skills}>
        {unit.skills.map((item) => (
          <SkillStatusIcon
            key={item.skill}
            skill={item.skill}
            status={item.status}
          />
        ))}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    minHeight: 44,
    paddingVertical: 10,
    paddingHorizontal: 12,
    gap: 8,
    borderRadius: 16,
  },
  current: {
    backgroundColor: colors.auroraTealSoft,
  },
  locked: {
    opacity: 0.5,
  },
  pressed: {
    opacity: 0.85,
  },
  titleBlock: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  title: {
    flex: 1,
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "700",
  },
  currentBadge: {
    color: colors.auroraTeal,
    fontSize: 11,
    fontWeight: "700",
  },
  completeHint: {
    color: colors.success,
    fontSize: 11,
    fontWeight: "700",
  },
  skills: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
});
