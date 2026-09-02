/**
 * Progress — live XP/level/streak snapshot, six-dimension speaking
 * radar (SN-017), CanadaReady™ scorecard (SN-048), plus C9 four-skill
 * levels from GET /api/progress/skills (C2). Existing elements stay.
 */
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withTiming,
} from "react-native-reanimated";
import { Circle as CircleSvg, Line, Polygon, Svg } from "react-native-svg";
import {
  Award,
  ChevronRight,
  Flame,
  RefreshCw,
  Trophy,
  Zap,
} from "lucide-react-native";

import { GlassCard } from "../../src/components/GlassCard";
import { FourSkillCard } from "../../src/components/progress/FourSkillCard";
import { radarVertices } from "../../src/components/progress/radar";
import {
  fetchGamificationSummary,
  fetchScorecard,
  fetchSkillProgress,
  type GamificationSummary,
  type Scorecard,
  type SkillProgress,
} from "../../src/api/client";
import { useAuthStore } from "../../src/stores/authStore";
import { colors } from "../../src/theme/colors";

const RADAR_SIZE = 220;
const RADAR_CENTER = RADAR_SIZE / 2;
const RADAR_RADIUS = RADAR_CENTER - 14;
const LABEL_CONTAINER_PAD = 46;

const DIMENSIONS = [
  { key: "fluency_score", label: "Fluency" },
  { key: "pronunciation_score", label: "Pronunciation" },
  { key: "grammar_score", label: "Grammar" },
  { key: "vocabulary_score", label: "Vocabulary" },
  { key: "coherence_score", label: "Coherence" },
  { key: "task_completion_score", label: "Task" },
] as const;

export { radarVertices };

function polygonPoints(values: number[]): string {
  return radarVertices(values, RADAR_CENTER, RADAR_RADIUS)
    .map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`)
    .join(" ");
}

function outerRingPoints(ringFraction: number): string {
  return polygonPoints(DIMENSIONS.map(() => 100 * ringFraction));
}

function SkillRadar({ values }: { values: number[] }): JSX.Element {
  const dataPoints = radarVertices(values, RADAR_CENTER, RADAR_RADIUS);
  const labelPoints = radarVertices(
    DIMENSIONS.map(() => 100),
    RADAR_CENTER,
    RADAR_RADIUS + 20,
  );

  return (
    <View
      style={[
        styles.radarWrap,
        {
          height: RADAR_SIZE,
          width: RADAR_SIZE,
          marginHorizontal: LABEL_CONTAINER_PAD - 14,
        },
      ]}
    >
      <Svg width={RADAR_SIZE} height={RADAR_SIZE}>
        {[1, 0.66, 0.33].map((ring) => (
          <Polygon
            key={ring}
            points={outerRingPoints(ring)}
            fill="none"
            stroke={colors.glassBorder}
            strokeWidth={1}
          />
        ))}
        {DIMENSIONS.map((_, index) => {
          const angle =
            (Math.PI * 2 * index) / DIMENSIONS.length - Math.PI / 2;
          return (
            <Line
              key={`axis-${index}`}
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
          points={polygonPoints(values)}
          fill="rgba(14, 165, 233, 0.25)"
          stroke={colors.auroraTeal}
          strokeWidth={2}
        />
        {dataPoints.map((point, index) => (
          <CircleSvg
            key={`dot-${DIMENSIONS[index].key}`}
            cx={point.x}
            cy={point.y}
            r={3}
            fill={colors.auroraTeal}
          />
        ))}
      </Svg>
      {labelPoints.map((point, index) => (
        <Text
          key={`label-${DIMENSIONS[index].key}`}
          style={[styles.radarLabel, { left: point.x - 44, top: point.y - 7 }]}
          allowFontScaling={false}
        >
          {DIMENSIONS[index].label}
        </Text>
      ))}
    </View>
  );
}

function LevelProgressBar({ percent }: { percent: number }): JSX.Element {
  const width = useSharedValue(0);

  useEffect(() => {
    width.value = withDelay(
      150,
      withTiming(percent, { duration: 600, easing: Easing.out(Easing.quad) }),
    );
  }, [percent, width]);

  const fillStyle = useAnimatedStyle(() => ({ width: `${width.value}%` }));

  return (
    <View style={styles.barTrack}>
      <Animated.View style={[styles.barFill, fillStyle]} />
    </View>
  );
}

/** Prominent CanadaReady™ Scorecard entry (SN-048) opening the screen. */
function ScorecardEntryCard({
  scorecard,
  onPress,
}: {
  scorecard: Scorecard | null;
  onPress: () => void;
}): JSX.Element {
  const score = scorecard?.canada_ready_score ?? 0;
  const badgeColor =
    scorecard?.badge.code === "canada-ready"
      ? colors.success
      : scorecard?.badge.code === "confident-colleague"
        ? colors.auroraTeal
        : colors.warmCoral;
  return (
    <GlassCard style={styles.scorecardEntry}>
      <Pressable
        style={styles.scorecardEntryPressable}
        accessibilityLabel="Open your CanadaReady Scorecard"
        onPress={onPress}
      >
        <View style={[styles.scorecardIconWell, { backgroundColor: `${badgeColor}E6` }]}>
          <Award color="#FFFFFF" size={22} />
        </View>
        <View style={styles.scorecardInfo}>
          <Text style={styles.scorecardTitle}>CanadaReady™ Scorecard</Text>
          <Text style={styles.scorecardBadge}>
            {scorecard?.badge.title ?? "First Steps"}
          </Text>
          <View style={styles.scorecardBarTrack}>
            <View
              style={[
                styles.scorecardBarFill,
                { width: `${Math.max(2, score)}%`, backgroundColor: badgeColor },
              ]}
            />
          </View>
        </View>
        <View style={styles.scorecardScoreBlock}>
          <Text style={[styles.scorecardScore, { color: badgeColor }]}>
            {score}
          </Text>
          <ChevronRight color={colors.textTertiary} size={18} />
        </View>
      </Pressable>
    </GlassCard>
  );
}

export default function ProgressScreen(): JSX.Element {
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const user = useAuthStore((state) => state.user);
  const [summary, setSummary] = useState<GamificationSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // SN-048: the scorecard entry needs the live badge + score; degrade
  // silently (entry stays tappable) when the fetch fails.
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [skillProgress, setSkillProgress] = useState<SkillProgress | null>(
    null,
  );
  const [skillError, setSkillError] = useState(false);

  const loadSummary = useCallback(async (): Promise<void> => {
    try {
      setSummary(await fetchGamificationSummary());
      setErrorText(null);
    } catch {
      setErrorText("Progress needs a connection — pull to retry once you're back online.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    let cancelled = false;
    fetchScorecard()
      .then((data) => {
        if (!cancelled) {
          setScorecard(data);
        }
      })
      .catch(() => {
        // No scorecard badge, no score — the card still opens the screen.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadSkillProgress = useCallback(async (): Promise<void> => {
    try {
      const payload = await fetchSkillProgress();
      setSkillProgress(payload);
      setSkillError(false);
    } catch {
      // Four-skill block is additive; XP / streak / badges / scorecard stay.
      setSkillError(true);
    }
  }, []);

  useEffect(() => {
    void loadSkillProgress();
  }, [loadSkillProgress]);

  const onRefresh = useCallback(async (): Promise<void> => {
    setRefreshing(true);
    await Promise.all([
      loadSummary(),
      fetchScorecard()
        .then(setScorecard)
        .catch(() => {}),
      loadSkillProgress(),
    ]);
    setRefreshing(false);
  }, [loadSummary, loadSkillProgress]);

  const skills = user?.skills ?? null;
  const radarValues = skills === null
    ? DIMENSIONS.map(() => 0)
    : DIMENSIONS.map((dimension) => skills[dimension.key]);
  const levelPercent =
    summary === null || summary.next_level_xp_threshold <= 0
      ? 0
      : Math.min(
          100,
          Math.round(
            (summary.progress_to_next_level / summary.next_level_xp_threshold) * 100,
          ),
        );

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingTop: insets.top + 20, paddingBottom: 120 },
      ]}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => {
            void onRefresh();
          }}
          tintColor={colors.auroraTeal}
        />
      }
    >
      <Text style={styles.heading}>Progress</Text>

      {/* SN-048: CanadaReady™ Scorecard entry — always visible so the
          screen is discoverable even before the first session. */}
      <ScorecardEntryCard
        scorecard={scorecard}
        onPress={() => router.push("/scorecard")}
      />

      {skillProgress !== null ? (
        <FourSkillCard progress={skillProgress} />
      ) : null}

      {skillError && skillProgress === null ? (
        <GlassCard style={styles.skillErrorCard}>
          <Text style={styles.skillErrorText}>
            Skill levels need a connection — the rest of your progress is still
            here.
          </Text>
          <Pressable
            style={styles.retryButton}
            accessibilityLabel="Retry loading skill levels"
            onPress={() => {
              void loadSkillProgress();
            }}
          >
            <RefreshCw color={colors.auroraTeal} size={16} />
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        </GlassCard>
      ) : null}

      {isLoading ? (
        <ActivityIndicator color={colors.auroraTeal} style={styles.spinner} />
      ) : null}

      {errorText !== null ? (
        <GlassCard style={styles.errorCard}>
          <Text style={styles.errorText}>{errorText}</Text>
          <Pressable
            style={styles.retryButton}
            accessibilityLabel="Retry loading progress"
            onPress={() => {
              setIsLoading(true);
              void loadSummary();
            }}
          >
            <RefreshCw color={colors.auroraTeal} size={16} />
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        </GlassCard>
      ) : null}

      {summary !== null ? (
        <>
          <View style={styles.summaryRow}>
            <GlassCard style={styles.summaryCard}>
              <Zap color={colors.auroraTeal} size={20} />
              <Text style={styles.summaryValue}>Level {summary.level}</Text>
              <Text style={styles.summaryLabel}>{summary.xp_total} XP total</Text>
            </GlassCard>
            <GlassCard style={styles.summaryCard}>
              <Flame color={colors.warmCoral} size={20} />
              <Text style={styles.summaryValue}>
                {summary.current_streak} day{summary.current_streak === 1 ? "" : "s"}
              </Text>
              <Text style={styles.summaryLabel}>Current streak</Text>
            </GlassCard>
            <GlassCard style={styles.summaryCard}>
              <Trophy color={colors.success} size={20} />
              <Text style={styles.summaryValue}>
                {summary.longest_streak} day{summary.longest_streak === 1 ? "" : "s"}
              </Text>
              <Text style={styles.summaryLabel}>Longest streak</Text>
            </GlassCard>
          </View>

          <GlassCard style={styles.radarCard}>
            <Text style={styles.cardTitle}>Skill radar</Text>
            <Text style={styles.cardSubtitle}>
              CLB-inspired six-dimension view of your speaking readiness.
            </Text>
            <SkillRadar values={radarValues} />
            {skills === null ? (
              <Text style={styles.radarEmptyNote}>
                Finish your first session to unlock real scores.
              </Text>
            ) : (
              <Text style={styles.compositeNote}>
                CanadaReady™ composite: {skills.canada_ready_score.toFixed(0)} / 100
              </Text>
            )}
          </GlassCard>

          <GlassCard style={styles.levelCard}>
            <Text style={styles.cardTitle}>Level {summary.level}</Text>
            <Text style={styles.cardSubtitle}>
              {summary.progress_to_next_level} / {summary.next_level_xp_threshold} XP
              into this level · {summary.xp_today} XP today
            </Text>
            <LevelProgressBar percent={levelPercent} />
          </GlassCard>

          <GlassCard style={styles.badgeCard}>
            <Text style={styles.cardTitle}>Badges</Text>
            {summary.badges.length === 0 ? (
              <Text style={styles.cardSubtitle}>
                Milestone badges land here after your sessions.
              </Text>
            ) : (
              <View style={styles.badgeRow}>
                {summary.badges.map((badge) => (
                  <View key={badge.code} style={styles.badgeChip}>
                    <Trophy color={colors.success} size={12} />
                    <Text style={styles.badgeChipText}>{badge.title}</Text>
                  </View>
                ))}
              </View>
            )}
          </GlassCard>
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.nightSky,
  },
  content: {
    paddingHorizontal: 20,
    gap: 16,
  },
  heading: {
    color: colors.textPrimary,
    fontSize: 26,
    fontWeight: "700",
    marginBottom: 4,
  },
  scorecardEntry: {
    padding: 16,
  },
  scorecardEntryPressable: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  scorecardIconWell: {
    width: 48,
    height: 48,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  scorecardInfo: {
    flex: 1,
    gap: 4,
  },
  scorecardTitle: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "700",
  },
  scorecardBadge: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  scorecardBarTrack: {
    height: 5,
    borderRadius: 3,
    backgroundColor: colors.nightSkyDeep,
    overflow: "hidden",
    marginTop: 2,
  },
  scorecardBarFill: {
    height: "100%",
    borderRadius: 3,
  },
  scorecardScoreBlock: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  scorecardScore: {
    fontSize: 20,
    fontWeight: "800",
  },
  spinner: {
    paddingVertical: 24,
  },
  errorCard: {
    alignItems: "center",
    gap: 10,
    paddingVertical: 20,
  },
  errorText: {
    color: colors.error,
    fontSize: 13,
    lineHeight: 18,
    textAlign: "center",
  },
  skillErrorCard: {
    alignItems: "center",
    gap: 10,
    paddingVertical: 16,
  },
  skillErrorText: {
    color: colors.textTertiary,
    fontSize: 13,
    lineHeight: 18,
    textAlign: "center",
  },
  retryButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: colors.nightSkyDeep,
  },
  retryText: {
    color: colors.auroraTeal,
    fontSize: 13,
    fontWeight: "700",
  },
  summaryRow: {
    flexDirection: "row",
    gap: 12,
  },
  summaryCard: {
    flex: 1,
    alignItems: "center",
    gap: 4,
    padding: 16,
  },
  summaryValue: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "800",
    textAlign: "center",
  },
  summaryLabel: {
    color: colors.textSecondary,
    fontSize: 11,
    fontWeight: "600",
    textAlign: "center",
  },
  radarCard: {
    gap: 6,
    alignItems: "center",
  },
  cardTitle: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
    alignSelf: "flex-start",
  },
  cardSubtitle: {
    color: colors.textTertiary,
    fontSize: 12,
    alignSelf: "flex-start",
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
  radarEmptyNote: {
    color: colors.textTertiary,
    fontSize: 12,
    textAlign: "center",
  },
  compositeNote: {
    color: colors.success,
    fontSize: 12,
    fontWeight: "700",
    textAlign: "center",
  },
  levelCard: {
    gap: 10,
  },
  badgeCard: {
    gap: 10,
  },
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  badgeChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.successSoft,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  badgeChipText: {
    color: colors.success,
    fontSize: 11,
    fontWeight: "700",
  },
  barTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.nightSkyDeep,
    overflow: "hidden",
  },
  barFill: {
    height: "100%",
    borderRadius: 3,
    backgroundColor: colors.auroraTeal,
  },
});
