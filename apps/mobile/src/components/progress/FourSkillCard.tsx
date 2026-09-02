/**
 * C9 four-skill Progress block: Display/Readiness levels, radar, bars,
 * and the C2 §5.6 imbalance alert. Discrete levels only — no invented
 * mastery percentages. Plots onto the existing react-native-svg stack.
 */
import { useEffect, useState } from "react";
import { AccessibilityInfo, StyleSheet, Text, View } from "react-native";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withTiming,
} from "react-native-reanimated";
import { Circle as CircleSvg, Line, Polygon, Svg } from "react-native-svg";

import type { SkillLevel, SkillProgress } from "../../api/client";
import { colors } from "../../theme/colors";
import { GlassCard } from "../GlassCard";
import { radarVertices } from "./radar";

export const MAX_SKILL_LEVEL = 9;

const RADAR_SIZE = 220;
const RADAR_CENTER = RADAR_SIZE / 2;
const RADAR_RADIUS = RADAR_CENTER - 14;
const LABEL_PAD = 46;

const SKILL_META: Record<string, { emoji: string; label: string }> = {
  speaking: { emoji: "🗣️", label: "Speaking" },
  listening: { emoji: "🎧", label: "Listening" },
  reading: { emoji: "📖", label: "Reading" },
  writing: { emoji: "✍️", label: "Writing" },
};

export function levelBarPercent(level: number): number {
  const clamped = Math.min(MAX_SKILL_LEVEL, Math.max(0, level));
  return Math.round((clamped / MAX_SKILL_LEVEL) * 100);
}

export function skillLabel(skill: string): string {
  return SKILL_META[skill]?.label ?? skill;
}

export function FourSkillCard({
  progress,
}: {
  progress: SkillProgress;
}): JSX.Element {
  const radarValues = progress.skills.map((item) => levelBarPercent(item.level));
  const radarSummary = progress.skills
    .map((item) => `${skillLabel(item.skill)} Level ${item.level}`)
    .join(". ");
  const showAlert = progress.imbalance.priority !== "balanced";

  return (
    <GlassCard style={styles.card}>
      <Text style={styles.sectionTitle}>Your skills</Text>
      <Text
        accessibilityRole="header"
        accessibilityLabel={`Display Level ${progress.display_level}`}
        style={styles.displayLevel}
      >
        Level {progress.display_level}
      </Text>
      <Text
        accessibilityLabel={`Readiness Level ${progress.readiness_level}`}
        style={styles.readiness}
      >
        Readiness Level {progress.readiness_level}
      </Text>
      <Text style={styles.legalNote}>
        Sonolo levels — CLB-inspired, CEFR-aligned. Not an official
        certification.
      </Text>

      <FourSkillRadar skills={progress.skills} values={radarValues} />
      <Text
        accessibilityRole="image"
        accessibilityLabel={`Four-skill radar. ${radarSummary}`}
        style={styles.radarSrOnly}
      />

      <Text style={styles.barsTitle}>Per-skill progress</Text>
      {progress.skills.map((item) => (
        <SkillLevelBar key={item.skill} item={item} />
      ))}

      {showAlert ? (
        <View
          accessibilityRole="alert"
          accessibilityLabel={progress.imbalance.message}
          style={[
            styles.alert,
            progress.imbalance.priority === "critical"
              ? styles.alertCritical
              : styles.alertHigh,
          ]}
        >
          <Text style={styles.alertText}>{progress.imbalance.message}</Text>
        </View>
      ) : null}
    </GlassCard>
  );
}

function FourSkillRadar({
  skills,
  values,
}: {
  skills: SkillLevel[];
  values: number[];
}): JSX.Element {
  const dataPoints = radarVertices(values, RADAR_CENTER, RADAR_RADIUS);
  const labelPoints = radarVertices(
    values.map(() => 100),
    RADAR_CENTER,
    RADAR_RADIUS + 20,
  );
  const rings = [1, 0.66, 0.33].map((ring) =>
    radarVertices(
      values.map(() => 100 * ring),
      RADAR_CENTER,
      RADAR_RADIUS,
    )
      .map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`)
      .join(" "),
  );
  const polygon = dataPoints
    .map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`)
    .join(" ");

  return (
    <View
      style={[
        styles.radarWrap,
        {
          height: RADAR_SIZE,
          width: RADAR_SIZE,
          marginHorizontal: LABEL_PAD - 14,
        },
      ]}
    >
      <Svg width={RADAR_SIZE} height={RADAR_SIZE}>
        {rings.map((points) => (
          <Polygon
            key={points}
            points={points}
            fill="none"
            stroke={colors.glassBorder}
            strokeWidth={1}
          />
        ))}
        {values.map((_, index) => {
          const angle = (Math.PI * 2 * index) / values.length - Math.PI / 2;
          return (
            <Line
              key={`axis-${skills[index]?.skill ?? index}`}
              x1={RADAR_CENTER}
              y1={RADAR_CENTER}
              x2={RADAR_CENTER + RADAR_RADIUS * Math.cos(angle)}
              y2={RADAR_CENTER + RADAR_RADIUS * Math.sin(angle)}
              stroke={colors.glassBorder}
              strokeWidth={1}
            />
          );
        })}
        <Polygon
          points={polygon}
          fill="rgba(14, 165, 233, 0.25)"
          stroke={colors.auroraTeal}
          strokeWidth={2}
        />
        {dataPoints.map((point, index) => (
          <CircleSvg
            key={`dot-${skills[index]?.skill ?? index}`}
            cx={point.x}
            cy={point.y}
            r={3}
            fill={colors.auroraTeal}
          />
        ))}
      </Svg>
      {labelPoints.map((point, index) => {
        const meta = SKILL_META[skills[index]?.skill ?? ""] ?? {
          emoji: "",
          label: skills[index]?.skill ?? "",
        };
        return (
          <Text
            key={`label-${skills[index]?.skill ?? index}`}
            style={[styles.radarLabel, { left: point.x - 44, top: point.y - 10 }]}
          >
            {meta.emoji} {meta.label}
          </Text>
        );
      })}
    </View>
  );
}

function SkillLevelBar({ item }: { item: SkillLevel }): JSX.Element {
  const meta = SKILL_META[item.skill] ?? { emoji: "", label: item.skill };
  const percent = levelBarPercent(item.level);
  const [reduceMotion, setReduceMotion] = useState(false);
  const width = useSharedValue(0);

  useEffect(() => {
    let cancelled = false;
    const query = AccessibilityInfo.isReduceMotionEnabled;
    if (typeof query === "function") {
      void query.call(AccessibilityInfo).then((value) => {
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
    if (reduceMotion) {
      width.value = percent;
      return;
    }
    width.value = withDelay(
      150,
      withTiming(percent, { duration: 600, easing: Easing.out(Easing.quad) }),
    );
  }, [percent, reduceMotion, width]);

  const fillStyle = useAnimatedStyle(() => ({ width: `${width.value}%` }));

  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={`${meta.label} Level ${item.level} of ${MAX_SKILL_LEVEL}`}
      style={styles.barRow}
    >
      <Text style={styles.barLabel}>
        {meta.emoji} {meta.label} Level {item.level}
      </Text>
      <View style={styles.barTrack}>
        <Animated.View style={[styles.barFill, fillStyle]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: 8,
  },
  sectionTitle: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
  },
  displayLevel: {
    color: colors.textPrimary,
    fontSize: 26,
    fontWeight: "800",
  },
  readiness: {
    color: colors.textSecondary,
    fontSize: 14,
    fontWeight: "600",
  },
  legalNote: {
    color: colors.textTertiary,
    fontSize: 11,
    lineHeight: 16,
  },
  radarWrap: {
    marginTop: 8,
    alignSelf: "center",
  },
  radarLabel: {
    position: "absolute",
    width: 88,
    color: colors.textSecondary,
    fontSize: 10,
    fontWeight: "600",
    textAlign: "center",
  },
  radarSrOnly: {
    height: 0,
    overflow: "hidden",
  },
  barsTitle: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "700",
    marginTop: 8,
  },
  barRow: {
    minHeight: 44,
    gap: 6,
    justifyContent: "center",
  },
  barLabel: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "600",
  },
  barTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.nightSkyDeep,
    overflow: "hidden",
  },
  barFill: {
    height: "100%",
    borderRadius: 4,
    backgroundColor: colors.auroraTeal,
  },
  alert: {
    marginTop: 8,
    borderRadius: 16,
    padding: 12,
    minHeight: 44,
    justifyContent: "center",
  },
  alertCritical: {
    backgroundColor: "rgba(248, 113, 113, 0.16)",
  },
  alertHigh: {
    backgroundColor: "rgba(251, 191, 36, 0.16)",
  },
  alertText: {
    color: colors.textPrimary,
    fontSize: 13,
    lineHeight: 18,
    fontWeight: "600",
  },
});
