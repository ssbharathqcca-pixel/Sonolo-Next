/**
 * C8 band card: active expanded with teal glow, completed collapsed,
 * locked grayed with unlock condition.
 */
import { useEffect, useState } from "react";
import {
  AccessibilityInfo,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";
import { Lock } from "lucide-react-native";

import type { JourneyBand, JourneyUnit } from "../../api/client";
import { colors } from "../../theme/colors";
import { GlassCard } from "../GlassCard";
import { UnitRow } from "./UnitRow";

export function bandAccessibilityLabel(band: JourneyBand): string {
  if (band.status === "locked") {
    const condition = band.unlock_condition ?? "Locked";
    return `${band.title}, locked. ${condition}`;
  }
  if (band.status === "completed") {
    return `${band.title}, complete`;
  }
  return `${band.title}, active`;
}

export function BandCard({
  band,
  expanded,
  onToggle,
  onUnitPress,
}: {
  band: JourneyBand;
  expanded: boolean;
  onToggle: (band: JourneyBand) => void;
  onUnitPress: (unit: JourneyUnit) => void;
}): JSX.Element {
  const locked = band.status === "locked";
  const active = band.status === "active";
  const completed = band.status === "completed";
  const [reduceMotion, setReduceMotion] = useState(false);
  const glow = useSharedValue(active ? 0.35 : 0);

  useEffect(() => {
    let cancelled = false;
    const reduceMotionQuery = AccessibilityInfo.isReduceMotionEnabled;
    if (typeof reduceMotionQuery === "function") {
      void reduceMotionQuery.call(AccessibilityInfo).then((value) => {
        if (!cancelled) {
          setReduceMotion(value);
        }
      });
    }
    const subscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduceMotion,
    );
    return () => {
      cancelled = true;
      if (subscription != null && typeof subscription.remove === "function") {
        subscription.remove();
      }
    };
  }, []);

  useEffect(() => {
    if (!active || reduceMotion) {
      glow.value = active ? 0.45 : 0;
      return;
    }
    glow.value = withRepeat(
      withTiming(0.75, { duration: 1400, easing: Easing.inOut(Easing.quad) }),
      -1,
      true,
    );
  }, [active, glow, reduceMotion]);

  const glowStyle = useAnimatedStyle(() => ({
    shadowOpacity: glow.value,
  }));

  return (
    <Animated.View style={[active && styles.glowWrap, active && glowStyle]}>
      <GlassCard
        style={[
          styles.card,
          active && styles.active,
          completed && styles.completed,
          locked && styles.locked,
        ]}
      >
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={bandAccessibilityLabel(band)}
          onPress={() => {
            onToggle(band);
          }}
          style={styles.header}
        >
          <Text style={styles.icon}>{band.icon}</Text>
          <View style={styles.headerText}>
            <Text style={styles.title}>{band.title}</Text>
            <Text style={styles.subtitle}>{band.subtitle}</Text>
          </View>
          {completed ? (
            <View style={styles.completeBadge}>
              <Text style={styles.completeBadgeText}>✅ Complete</Text>
            </View>
          ) : null}
          {locked ? (
            <View style={styles.lockWell} accessibilityLabel="Locked">
              <Lock color={colors.textTertiary} size={16} />
            </View>
          ) : null}
        </Pressable>
        {locked && band.unlock_condition ? (
          <Text style={styles.unlock}>{band.unlock_condition}</Text>
        ) : null}
        {expanded ? (
          <View style={styles.units}>
            {band.units.map((unit) => (
              <UnitRow key={unit.id} unit={unit} onPress={onUnitPress} />
            ))}
          </View>
        ) : null}
      </GlassCard>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  glowWrap: {
    shadowColor: colors.auroraTeal,
    shadowOffset: { width: 0, height: 0 },
    shadowRadius: 14,
  },
  card: {
    padding: 16,
    gap: 10,
  },
  active: {
    borderWidth: 2,
    borderColor: colors.auroraTeal,
  },
  completed: {
    borderWidth: 1,
    borderColor: colors.success,
  },
  locked: {
    opacity: 0.5,
  },
  header: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  icon: {
    fontSize: 22,
  },
  headerText: {
    flex: 1,
    gap: 2,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "700",
  },
  subtitle: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  completeBadge: {
    borderRadius: 999,
    backgroundColor: colors.successSoft,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  completeBadgeText: {
    color: colors.success,
    fontSize: 11,
    fontWeight: "700",
  },
  lockWell: {
    width: 32,
    height: 32,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.nightSkyDeep,
  },
  unlock: {
    color: colors.textTertiary,
    fontSize: 12,
    lineHeight: 16,
  },
  units: {
    gap: 4,
    marginTop: 4,
  },
});
