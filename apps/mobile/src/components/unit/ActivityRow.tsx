/**
 * C10 activity row: name, type, completion, lock, tap when actionable.
 */
import { Pressable, StyleSheet, Text, View } from "react-native";
import { CheckCircle2, ChevronRight, Lock } from "lucide-react-native";

import { colors } from "../../theme/colors";
import { GlassCard } from "../GlassCard";
import {
  activityAccessibilityLabel,
  type UnitActivity,
} from "./unitActivities";

export function ActivityRow({
  activity,
  onPress,
}: {
  activity: UnitActivity;
  onPress: (activity: UnitActivity) => void;
}): JSX.Element {
  const locked = activity.state === "locked";
  const completed = activity.state === "completed";

  return (
    <GlassCard style={[styles.card, locked && styles.locked]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={activityAccessibilityLabel(activity)}
        accessibilityState={{ disabled: locked }}
        disabled={locked}
        onPress={() => {
          if (!locked) {
            onPress(activity);
          }
        }}
        style={styles.pressable}
      >
        <View style={styles.iconWell}>
          {completed ? (
            <CheckCircle2 color={colors.success} size={20} />
          ) : locked ? (
            <Lock color={colors.textTertiary} size={18} />
          ) : (
            <ChevronRight color={colors.auroraTeal} size={20} />
          )}
        </View>
        <View style={styles.copy}>
          <Text style={styles.title}>{activity.title}</Text>
          <Text style={styles.subtitle}>{activity.subtitle}</Text>
        </View>
        {completed ? (
          <Text style={styles.completeChip}>Complete</Text>
        ) : null}
      </Pressable>
    </GlassCard>
  );
}

const styles = StyleSheet.create({
  card: {
    padding: 0,
  },
  locked: {
    opacity: 0.5,
  },
  pressable: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    paddingHorizontal: 14,
  },
  iconWell: {
    width: 36,
    height: 36,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.nightSkyDeep,
  },
  copy: {
    flex: 1,
    gap: 2,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "700",
  },
  subtitle: {
    color: colors.textTertiary,
    fontSize: 12,
    lineHeight: 16,
  },
  completeChip: {
    color: colors.success,
    fontSize: 11,
    fontWeight: "700",
  },
});
