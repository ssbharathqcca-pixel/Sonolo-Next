/**
 * C10 Unit Detail — §15.3 activity sequence for one curriculum unit.
 * Catalog from GET /learn/units/{code} (C1). Completion from GET
 * /learn/journey skill flags (C0/C8). Does not reimplement C8 prereqs.
 * Skill blocks are independently available. Unit Test stays locked
 * until all four skill flags are complete.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ChevronLeft } from "lucide-react-native";

import { GlassCard } from "../../src/components/GlassCard";
import { ActivityRow } from "../../src/components/unit/ActivityRow";
import {
  buildUnitActivities,
  findJourneyUnit,
  type UnitActivity,
} from "../../src/components/unit/unitActivities";
import {
  fetchJourney,
  fetchUnit,
  type JourneyMapData,
  type UnitDetail,
} from "../../src/api/client";
import { colors } from "../../src/theme/colors";

const BAND_LABEL: Record<string, string> = {
  foundation: "Foundation Band",
  middle: "Middle Band",
  advanced: "Advanced Band",
};

export default function UnitDetailScreen(): JSX.Element {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const params = useLocalSearchParams<{ id?: string }>();
  const unitCode = params.id ?? "";

  const [unit, setUnit] = useState<UnitDetail | null>(null);
  const [journey, setJourney] = useState<JourneyMapData | null>(null);
  const [unitFailed, setUnitFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoaded(false);
    setUnitFailed(false);
    const [unitResult, journeyResult] = await Promise.allSettled([
      fetchUnit(unitCode),
      fetchJourney(),
    ]);
    if (unitResult.status === "fulfilled") {
      setUnit(unitResult.value);
    } else {
      setUnit(null);
      setUnitFailed(true);
    }
    if (journeyResult.status === "fulfilled") {
      setJourney(journeyResult.value);
    } else {
      setJourney(null);
    }
    setLoaded(true);
  }, [unitCode]);

  useEffect(() => {
    if (unitCode.length === 0) {
      return;
    }
    void load();
  }, [unitCode, load]);

  const journeyUnit = useMemo(
    () => findJourneyUnit(journey, unitCode),
    [journey, unitCode],
  );
  const activities = useMemo(
    () => buildUnitActivities(unit, journeyUnit),
    [unit, journeyUnit],
  );

  const title = unit?.title ?? journeyUnit?.title ?? unitCode;
  const icon = unit?.icon || "📘";
  const band = unit?.band ?? "";
  const story = unit?.story_chapter ?? "";
  const levelTarget = unit?.level_target;
  const unitComplete = journeyUnit?.status === "completed";

  const handleActivityPress = useCallback(
    (activity: UnitActivity): void => {
      if (activity.state === "locked" || activity.route === null) {
        return;
      }
      router.push(activity.route);
    },
    [router],
  );

  if (!loaded) {
    return (
      <View style={[styles.screen, { paddingTop: insets.top + 12 }]}>
        <ActivityIndicator color={colors.auroraTeal} style={styles.spinner} />
      </View>
    );
  }

  if (unit === null && journeyUnit === null) {
    return (
      <View style={[styles.screen, { paddingTop: insets.top + 12 }]}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Back to Learn"
          onPress={() => router.back()}
          style={styles.backHit}
        >
          <ChevronLeft color={colors.textPrimary} size={22} />
        </Pressable>
        <View style={styles.centeredNote}>
          <Text style={styles.noteTitle}>Unit unavailable</Text>
          <Text style={styles.noteBody}>
            {unitFailed
              ? "This unit needs a connection, or it is not published yet."
              : "This unit could not be found."}
          </Text>
        </View>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingTop: insets.top + 8, paddingBottom: 40 },
      ]}
      showsVerticalScrollIndicator={false}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Back to Learn"
        onPress={() => router.back()}
        style={styles.backHit}
      >
        <ChevronLeft color={colors.textPrimary} size={22} />
        <Text style={styles.backText}>Learn</Text>
      </Pressable>

      <GlassCard style={styles.headerCard}>
        <Text
          accessibilityRole="header"
          accessibilityLabel={`Unit ${unitCode}: ${title}`}
          style={styles.heading}
        >
          {icon} {unitCode}: {title}
        </Text>
        {band !== "" ? (
          <Text style={styles.meta}>
            {BAND_LABEL[band] ?? band}
            {levelTarget !== undefined ? ` · Level ${levelTarget}` : ""}
          </Text>
        ) : null}
        {story !== "" ? (
          <Text style={styles.story}>Story: “{story}”</Text>
        ) : null}
      </GlassCard>

      {unitComplete ? (
        <GlassCard style={styles.completeCard}>
          <Text style={styles.completeTitle}>✅ Unit complete</Text>
          <Text style={styles.completeBody}>
            You passed the unit test. Review any block, or continue on the
            Journey Map.
          </Text>
        </GlassCard>
      ) : null}

      {activities.map((activity) => (
        <ActivityRow
          key={activity.key}
          activity={activity}
          onPress={handleActivityPress}
        />
      ))}
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
    gap: 12,
  },
  spinner: {
    paddingVertical: 48,
  },
  backHit: {
    minHeight: 44,
    minWidth: 44,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    alignSelf: "flex-start",
  },
  backText: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "600",
  },
  headerCard: {
    gap: 6,
    padding: 16,
  },
  heading: {
    color: colors.textPrimary,
    fontSize: 22,
    fontWeight: "800",
  },
  meta: {
    color: colors.auroraTeal,
    fontSize: 13,
    fontWeight: "700",
  },
  story: {
    color: colors.textSecondary,
    fontSize: 14,
    lineHeight: 20,
  },
  completeCard: {
    gap: 6,
    padding: 16,
    borderColor: colors.success,
  },
  completeTitle: {
    color: colors.success,
    fontSize: 16,
    fontWeight: "700",
  },
  completeBody: {
    color: colors.textSecondary,
    fontSize: 13,
    lineHeight: 18,
  },
  centeredNote: {
    paddingHorizontal: 24,
    paddingTop: 40,
    gap: 8,
  },
  noteTitle: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
    textAlign: "center",
  },
  noteBody: {
    color: colors.textTertiary,
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center",
  },
});
