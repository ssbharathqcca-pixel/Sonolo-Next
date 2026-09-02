/**
 * Learn — C8 Journey Map at the top, then existing content rails
 * (packs, Culture Corner, Pronunciation Lab, Listening Gym, daily
 * quests, scenario library). Premium scenarios still open SN-026
 * paywall. The Journey Map is additive; rails are not replaced.
 */
import { useCallback, useEffect, useState } from "react";
import type { ComponentProps } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import Ionicons from "@expo/vector-icons/Ionicons";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withTiming,
} from "react-native-reanimated";
import {
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Lock,
  MessagesSquare,
  Target,
} from "lucide-react-native";

import { GlassCard } from "../../src/components/GlassCard";
import { JourneyMap } from "../../src/components/journey/JourneyMap";
import { PaywallModal } from "../../src/components/PaywallModal";
import {
  fetchJourney,
  fetchListeningDialogues,
  fetchMicrolessons,
  fetchPacks,
  fetchPronunciationDrills,
  fetchTodayQuests,
  type ContentPack,
  type JourneyMapData,
  type JourneyUnit,
  type ListeningDialogueSummary,
  type MicrolessonSummary,
  type PronunciationDrillSummary,
  type QuestResult,
  type Scenario,
} from "../../src/api/client";
import { isLockedForCaller } from "../../src/lib/scenarioAccess";
import { useAuthStore } from "../../src/stores/authStore";
import { useMicroProgressStore } from "../../src/stores/microProgressStore";
import { useScenarioStore } from "../../src/stores/scenarioStore";
import { colors } from "../../src/theme/colors";

/** Manifest icon names mapped onto their Ionicons glyphs (SN-030). */
const PACK_ICONS: Record<string, ComponentProps<typeof Ionicons>["name"]> = {
  briefcase: "briefcase",
  home: "home",
  map: "map",
  book: "book",
};

function packIcon(icon: string): ComponentProps<typeof Ionicons>["name"] {
  return PACK_ICONS[icon] ?? "book";
}

/**
 * Exact pack membership first (SN-035): scenarios carry the manifest
 * pack id they were seeded from, so filtering compares ids directly.
 * Catalogs cached before SN-035 have no pack_id and fall back to the
 * old category + language heuristic.
 */
function scenarioMatchesPack(scenario: Scenario, pack: ContentPack): boolean {
  if (scenario.pack_id) {
    return scenario.pack_id === pack.id;
  }
  return (
    scenario.category === pack.category &&
    (scenario.target_language ?? "")
      .toLowerCase()
      .startsWith(pack.language.toLowerCase())
  );
}

/**
 * The server counts scenarios per pack from Scenario.pack_id (SN-035);
 * prefer that over recounting the local list, which may be truncated.
 */
function countScenariosInPack(
  scenarios: Scenario[],
  pack: ContentPack,
): number {
  if (pack.scenario_count !== undefined) {
    return pack.scenario_count;
  }
  return scenarios.filter((scenario) => scenarioMatchesPack(scenario, pack))
    .length;
}

function difficultyTone(difficulty: number | null): {
  label: string;
  text: string;
  background: string;
} {
  if (difficulty === null || difficulty <= 2) {
    return { label: "Gentle", text: colors.success, background: colors.successSoft };
  }
  if (difficulty <= 3) {
    return {
      label: "Standard",
      text: colors.auroraTeal,
      background: colors.auroraTealSoft,
    };
  }
  return {
    label: "Challenge",
    text: colors.warmCoral,
    background: colors.warmCoralSoft,
  };
}

function QuestProgressBar({ percent }: { percent: number }): JSX.Element {
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

function DailyQuestCard({ quest }: { quest: QuestResult }): JSX.Element {
  const percent = Math.min(
    100,
    Math.round((quest.progress_count / Math.max(1, quest.target_count)) * 100),
  );

  return (
    <GlassCard style={styles.questCard}>
      <View style={styles.questHeader}>
        <View style={styles.questIconWell}>
          <Target color={colors.auroraTeal} size={18} />
        </View>
        <View style={styles.questInfo}>
          <Text style={styles.questTitle}>{quest.title}</Text>
          <Text style={styles.questMeta} numberOfLines={1}>
            {quest.description}
          </Text>
        </View>
        {quest.completed ? (
          <CheckCircle2 color={colors.success} size={20} />
        ) : (
          <View style={styles.xpChip}>
            <Text style={styles.xpChipText}>+{quest.reward_xp} XP</Text>
          </View>
        )}
      </View>
      <QuestProgressBar percent={percent} />
      <Text style={styles.questProgressText}>
        {quest.completed
          ? "Done — reward earned"
          : `${quest.progress_count} of ${quest.target_count}`}
      </Text>
    </GlassCard>
  );
}

function LibraryScenarioCard({
  scenario,
  isLocked,
  onPress,
}: {
  scenario: Scenario;
  isLocked: boolean;
  onPress: () => void;
}): JSX.Element {
  const tone = difficultyTone(scenario.difficulty);

  return (
    <GlassCard style={styles.libraryCard}>
      <Pressable
        style={styles.libraryPressable}
        accessibilityLabel={
          isLocked
            ? `Premium scenario: ${scenario.title}`
            : `Start scenario: ${scenario.title}`
        }
        onPress={onPress}
      >
        <View style={styles.libraryIconWell}>
          <MessagesSquare color={colors.auroraTeal} size={20} />
        </View>
        <View style={styles.libraryInfo}>
          <Text style={styles.libraryTitle} numberOfLines={1}>
            {scenario.title}
          </Text>
          <Text style={styles.libraryMeta}>{scenario.category}</Text>
        </View>
        {!isLocked ? (
          <>
            <View
              style={[styles.difficultyBadge, { backgroundColor: tone.background }]}
            >
              <Text style={[styles.difficultyText, { color: tone.text }]}>
                {tone.label}
              </Text>
            </View>
            <ChevronRight color={colors.textTertiary} size={18} />
          </>
        ) : null}
      </Pressable>
      {isLocked ? (
        // Translucent cover in place of a native blur (no expo-blur
        // dependency) — dims the card and carries the premium lock.
        <View style={styles.lockOverlay} pointerEvents="none">
          <View style={styles.lockWell}>
            <Lock color="#FFFFFF" size={18} />
          </View>
          <Text style={styles.lockLabel}>Premium</Text>
        </View>
      ) : null}
    </GlassCard>
  );
}

function PackCard({
  pack,
  scenarioCount,
  onPress,
}: {
  pack: ContentPack;
  scenarioCount: number;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      accessibilityLabel={`Learning pack: ${pack.title}`}
      onPress={onPress}
      style={({ pressed }) => [
        styles.packCard,
        // Solid theme color at 0.9 opacity (alpha "E6") — no gradient
        // dependency; the white border keeps it crisp on dark nights.
        { backgroundColor: `${pack.theme_color}E6` },
        pressed && styles.packCardPressed,
      ]}
    >
      <View style={styles.packIconWell}>
        <Ionicons name={packIcon(pack.icon)} size={22} color="#FFFFFF" />
      </View>
      <Text style={styles.packTitle} numberOfLines={1}>
        {pack.title}
      </Text>
      <Text style={styles.packDescription} numberOfLines={3}>
        {pack.description}
      </Text>
      <View style={styles.packBadge}>
        <Text style={styles.packBadgeText}>
          {scenarioCount} scenario{scenarioCount === 1 ? "" : "s"}
        </Text>
      </View>
    </Pressable>
  );
}

function CultureCornerCard({
  lesson,
  isDone,
  onPress,
}: {
  lesson: MicrolessonSummary;
  isDone: boolean;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      accessibilityLabel={`Culture lesson: ${lesson.title}`}
      onPress={onPress}
      style={({ pressed }) => [
        styles.microCard,
        // Solid theme color at 0.9 opacity — same treatment as pack cards.
        { backgroundColor: `${lesson.theme_color ?? "#E11D48"}E6` },
        pressed && styles.packCardPressed,
      ]}
    >
      <View style={styles.microCardHeader}>
        <View style={styles.microIconWell}>
          <Text style={styles.microIcon}>{lesson.icon ?? "🍁"}</Text>
        </View>
        {isDone ? (
          <View style={styles.microDoneBadge}>
            <CheckCircle2 color="#FFFFFF" size={14} />
          </View>
        ) : null}
      </View>
      <Text style={styles.microTitle} numberOfLines={2}>
        {lesson.title}
      </Text>
      <View style={styles.microMetaRow}>
        <BookOpen color="rgba(255, 255, 255, 0.85)" size={12} />
        <Text style={styles.microMeta}>
          {lesson.read_minutes} min{isDone ? " · Done" : ""}
        </Text>
      </View>
    </Pressable>
  );
}

function PronunciationCard({
  drill,
  isLocked,
  onPress,
}: {
  drill: PronunciationDrillSummary;
  isLocked: boolean;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      accessibilityLabel={`Pronunciation drill: ${drill.title}`}
      onPress={onPress}
      style={({ pressed }) => [
        styles.microCard,
        // Same solid theme-color card treatment as pack and culture cards.
        { backgroundColor: `${drill.theme_color ?? "#A78BFA"}E6` },
        pressed && styles.packCardPressed,
      ]}
    >
      <View style={styles.microCardHeader}>
        <View style={styles.microIconWell}>
          <Text style={styles.microIcon}>{drill.icon ?? "🗣️"}</Text>
        </View>
        {isLocked ? (
          <View style={styles.microDoneBadge}>
            <Lock color="#FFFFFF" size={14} />
          </View>
        ) : null}
      </View>
      <Text style={styles.microTitle} numberOfLines={2}>
        {drill.title}
      </Text>
      <Text style={styles.microMeta} numberOfLines={1}>
        {isLocked ? "Premium" : drill.focus}
      </Text>
    </Pressable>
  );
}

function ListeningCard({
  dialogue,
  isLocked,
  onPress,
}: {
  dialogue: ListeningDialogueSummary;
  isLocked: boolean;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      accessibilityLabel={`Listening dialogue: ${dialogue.title}`}
      onPress={onPress}
      style={({ pressed }) => [
        styles.microCard,
        // Same solid theme-color card treatment as the other rails.
        { backgroundColor: `${dialogue.theme_color ?? "#06B6D4"}E6` },
        pressed && styles.packCardPressed,
      ]}
    >
      <View style={styles.microCardHeader}>
        <View style={styles.microIconWell}>
          <Text style={styles.microIcon}>{dialogue.icon ?? "🎧"}</Text>
        </View>
        {isLocked ? (
          <View style={styles.microDoneBadge}>
            <Lock color="#FFFFFF" size={14} />
          </View>
        ) : null}
      </View>
      <Text style={styles.microTitle} numberOfLines={2}>
        {dialogue.title}
      </Text>
      <Text style={styles.microMeta} numberOfLines={1}>
        {isLocked ? "Premium" : dialogue.listening_focus}
      </Text>
    </Pressable>
  );
}

export default function LearnScreen(): JSX.Element {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [quests, setQuests] = useState<QuestResult[] | null>(null);
  const [questsUnavailable, setQuestsUnavailable] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [paywallVisible, setPaywallVisible] = useState(false);
  // SN-030: manifest packs behind the tappable card rail; SN-039 cards
  // navigate to the Pack Detail screen instead of filtering in place.
  const [packs, setPacks] = useState<ContentPack[]>([]);
  // SN-047: Culture Corner micro-lesson summaries for the card rail.
  const [microlessons, setMicrolessons] = useState<MicrolessonSummary[]>([]);
  // SN-049: Pronunciation Lab drill summaries for the card rail.
  const [pronunciationDrills, setPronunciationDrills] = useState<
    PronunciationDrillSummary[]
  >([]);
  // SN-050: Listening Gym dialogue summaries for the card rail.
  const [listeningDialogues, setListeningDialogues] = useState<
    ListeningDialogueSummary[]
  >([]);
  const [journey, setJourney] = useState<JourneyMapData | null>(null);

  const user = useAuthStore((state) => state.user);
  const scenarios = useScenarioStore((state) => state.scenarios);
  const isLoadingScenarios = useScenarioStore((state) => state.isLoading);
  const catalogLanguage = useScenarioStore((state) => state.language);
  const loadScenarios = useScenarioStore((state) => state.load);
  // SN-047: micro-lesson completion is device-local; hydrate once so a
  // finished lesson keeps its check after a relaunch.
  const isMicroHydrated = useMicroProgressStore((state) => state.isHydrated);
  const hydrateMicroProgress = useMicroProgressStore((state) => state.hydrate);
  const completedMicrolessonIds = useMicroProgressStore(
    (state) => state.completedMicrolessonIds,
  );

  useEffect(() => {
    if (!isMicroHydrated) {
      void hydrateMicroProgress();
    }
  }, [isMicroHydrated, hydrateMicroProgress]);

  // The catalog follows the account's content language (SN-020): it
  // loads when missing or stale and refetches when the preference
  // changes (e.g. from the Settings screen).
  const preferredLanguage = user?.preferred_language ?? "en";

  useEffect(() => {
    if (scenarios.length === 0 || catalogLanguage !== preferredLanguage) {
      void loadScenarios(preferredLanguage);
    }
  }, [scenarios.length, catalogLanguage, preferredLanguage, loadScenarios]);

  // Pack rail metadata is static manifest data (SN-030): fetch once on
  // mount alongside the catalog and degrade silently when offline —
  // the library below stays fully usable without the rail.
  useEffect(() => {
    let cancelled = false;
    fetchPacks()
      .then((manifestPacks) => {
        if (!cancelled) {
          setPacks(manifestPacks);
        }
      })
      .catch(() => {
        // No packs, no rail; nothing else on screen depends on it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Culture Corner summaries are static manifest data (SN-047): fetch
  // once on mount and degrade silently when offline, same as the pack
  // rail above. SN-049 passes the learner's preferred language so FR
  // users see the French pack and EN users the English one.
  useEffect(() => {
    let cancelled = false;
    fetchMicrolessons(preferredLanguage)
      .then((lessons) => {
        if (!cancelled) {
          setMicrolessons(lessons);
        }
      })
      .catch(() => {
        // No lessons, no Culture Corner rail; nothing else depends on it.
      });
    return () => {
      cancelled = true;
    };
  }, [preferredLanguage]);

  // Pronunciation Lab summaries are static manifest data (SN-049):
  // fetch once on mount and degrade silently when offline.
  useEffect(() => {
    let cancelled = false;
    fetchPronunciationDrills()
      .then((drills) => {
        if (!cancelled) {
          setPronunciationDrills(drills);
        }
      })
      .catch(() => {
        // No drills, no Pronunciation Lab rail; nothing else depends on it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Listening Gym summaries are static manifest data (SN-050): fetch
  // once on mount and degrade silently when offline.
  useEffect(() => {
    let cancelled = false;
    fetchListeningDialogues()
      .then((dialogues) => {
        if (!cancelled) {
          setListeningDialogues(dialogues);
        }
      })
      .catch(() => {
        // No dialogues, no Listening Gym rail; nothing else depends on it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Server tier truth beats a possibly stale cached catalog: once the
  // account is premium no card stays locked (SN-026).
  const isPremiumUser = user?.subscription_tier === "premium";

  const handleScenarioPress = useCallback(
    (scenario: Scenario): void => {
      if (isLockedForCaller(scenario, isPremiumUser)) {
        setPaywallVisible(true);
        return;
      }
      router.push({ pathname: "/session/[id]", params: { id: scenario.id } });
    },
    [isPremiumUser, router],
  );

  // SN-049: a locked pronunciation drill opens the paywall instead of
  // the player; unlocked ones jump straight into the drill.
  const handleDrillPress = useCallback(
    (drill: PronunciationDrillSummary): void => {
      if (drill.is_locked === true && !isPremiumUser) {
        setPaywallVisible(true);
        return;
      }
      router.push(`/pronunciation/${drill.id}`);
    },
    [isPremiumUser, router],
  );

  // SN-050: a locked listening dialogue opens the paywall instead of
  // the player; unlocked ones jump straight into the dialogue.
  const handleListeningPress = useCallback(
    (dialogue: ListeningDialogueSummary): void => {
      if (dialogue.is_locked === true && !isPremiumUser) {
        setPaywallVisible(true);
        return;
      }
      router.push(`/listening/${dialogue.id}`);
    },
    [isPremiumUser, router],
  );

  const loadQuests = useCallback(async (): Promise<void> => {
    try {
      const response = await fetchTodayQuests();
      setQuests(response.quests);
      setQuestsUnavailable(false);
    } catch {
      // Daily quests are live data; without a connection we say so and
      // keep the rest of the screen usable.
      setQuestsUnavailable(true);
    }
  }, []);

  const loadJourney = useCallback(async (): Promise<void> => {
    try {
      const payload = await fetchJourney();
      setJourney(payload);
    } catch {
      // Journey Map is additive; rails stay usable without it.
    }
  }, []);

  useEffect(() => {
    void loadQuests();
  }, [loadQuests]);

  useEffect(() => {
    void loadJourney();
  }, [loadJourney]);

  const onRefresh = useCallback(async (): Promise<void> => {
    setRefreshing(true);
    await Promise.all([
      loadQuests(),
      loadScenarios(preferredLanguage),
      loadJourney(),
    ]);
    setRefreshing(false);
  }, [loadQuests, loadScenarios, preferredLanguage, loadJourney]);

  const handleUnitPress = useCallback(
    (unit: JourneyUnit): void => {
      if (unit.status === "locked") {
        return;
      }
      router.push(`/unit/${unit.id}`);
    },
    [router],
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
      <Text style={styles.heading}>Learn</Text>
      <Text style={styles.subheading}>
        Short, real-life scenarios. Speak them until they feel boring — that's
        fluency.
      </Text>

      {journey !== null ? (
        <JourneyMap journey={journey} onUnitPress={handleUnitPress} />
      ) : null}

      {packs.length > 0 ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.packRail}
        >
          {packs.map((pack) => (
            <PackCard
              key={pack.id}
              pack={pack}
              scenarioCount={countScenariosInPack(scenarios, pack)}
              onPress={() => router.push(`/pack/${pack.id}`)}
            />
          ))}
        </ScrollView>
      ) : null}

      {microlessons.length > 0 ? (
        <View style={styles.microSection}>
          <Text style={styles.sectionTitle}>Culture Corner</Text>
          <Text style={styles.microSubtitle}>
            One-minute reads on the unwritten rules of fitting in.
          </Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.microRail}
          >
            {microlessons.map((lesson) => (
              <CultureCornerCard
                key={lesson.id}
                lesson={lesson}
                isDone={completedMicrolessonIds.includes(lesson.id)}
                onPress={() => router.push(`/microlesson/${lesson.id}`)}
              />
            ))}
          </ScrollView>
        </View>
      ) : null}

      {pronunciationDrills.length > 0 ? (
        <View style={styles.microSection}>
          <Text style={styles.sectionTitle}>Pronunciation Lab</Text>
          <Text style={styles.microSubtitle}>
            Canadian speech sounds — drill them until they feel like yours.
          </Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.microRail}
          >
            {pronunciationDrills.map((drill) => (
              <PronunciationCard
                key={drill.id}
                drill={drill}
                isLocked={drill.is_locked === true && !isPremiumUser}
                onPress={() => handleDrillPress(drill)}
              />
            ))}
          </ScrollView>
        </View>
      ) : null}

      {listeningDialogues.length > 0 ? (
        <View style={styles.microSection}>
          <Text style={styles.sectionTitle}>Listening Gym</Text>
          <Text style={styles.microSubtitle}>
            Train your ear on real Canadian conversations.
          </Text>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.microRail}
          >
            {listeningDialogues.map((dialogue) => (
              <ListeningCard
                key={dialogue.id}
                dialogue={dialogue}
                isLocked={dialogue.is_locked === true && !isPremiumUser}
                onPress={() => handleListeningPress(dialogue)}
              />
            ))}
          </ScrollView>
        </View>
      ) : null}

      {quests === null && !questsUnavailable ? (
        <ActivityIndicator
          color={colors.auroraTeal}
          style={styles.questsSpinner}
        />
      ) : null}

      {quests !== null && quests.length > 0 ? (
        <View style={styles.questsSection}>
          <Text style={styles.sectionTitle}>Today's quests</Text>
          {quests.map((quest) => (
            <DailyQuestCard key={quest.code} quest={quest} />
          ))}
        </View>
      ) : null}

      {questsUnavailable ? (
        <Text style={styles.offlineNote}>
          Quests need a connection — they'll reappear when you're back online.
        </Text>
      ) : null}

      <Text style={styles.sectionTitle}>Scenario library</Text>
      {isLoadingScenarios && scenarios.length === 0 ? (
        <ActivityIndicator
          color={colors.auroraTeal}
          style={styles.questsSpinner}
        />
      ) : null}
      {!isLoadingScenarios && scenarios.length === 0 ? (
        <Text style={styles.offlineNote}>
          Scenarios need a connection on first launch — they'll appear once
          you're back online.
        </Text>
      ) : null}
      {scenarios.map((scenario) => (
        <LibraryScenarioCard
          key={scenario.id}
          scenario={scenario}
          isLocked={isLockedForCaller(scenario, isPremiumUser)}
          onPress={() => handleScenarioPress(scenario)}
        />
      ))}

      <PaywallModal
        visible={paywallVisible}
        onClose={() => {
          setPaywallVisible(false);
        }}
      />
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
    gap: 14,
  },
  heading: {
    color: colors.textPrimary,
    fontSize: 26,
    fontWeight: "700",
  },
  subheading: {
    color: colors.textSecondary,
    fontSize: 14,
    lineHeight: 20,
    marginBottom: 4,
  },
  sectionTitle: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
    marginTop: 8,
  },
  questsSpinner: {
    paddingVertical: 12,
  },
  questsSection: {
    gap: 10,
  },
  questCard: {
    padding: 14,
    gap: 10,
  },
  questHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  questIconWell: {
    width: 38,
    height: 38,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.auroraTealSoft,
  },
  questInfo: {
    flex: 1,
    gap: 2,
  },
  questTitle: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "700",
  },
  questMeta: {
    color: colors.textTertiary,
    fontSize: 12,
  },
  xpChip: {
    borderRadius: 999,
    backgroundColor: colors.auroraTealSoft,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  xpChipText: {
    color: colors.auroraTeal,
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
  questProgressText: {
    color: colors.textTertiary,
    fontSize: 11,
    fontWeight: "600",
  },
  offlineNote: {
    color: colors.textTertiary,
    fontSize: 13,
    lineHeight: 18,
  },
  packRail: {
    paddingRight: 4,
  },
  microSection: {
    gap: 10,
  },
  microSubtitle: {
    color: colors.textTertiary,
    fontSize: 13,
    lineHeight: 18,
  },
  microRail: {
    paddingRight: 4,
    paddingTop: 2,
  },
  microCard: {
    width: 210,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.2)",
    padding: 14,
    marginRight: 12,
    gap: 8,
    shadowColor: "#000000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
    elevation: 4,
  },
  microCardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  microIconWell: {
    width: 34,
    height: 34,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255, 255, 255, 0.18)",
  },
  microIcon: {
    fontSize: 18,
  },
  microDoneBadge: {
    width: 22,
    height: 22,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255, 255, 255, 0.25)",
  },
  microTitle: {
    color: "#FFFFFF",
    fontSize: 14,
    fontWeight: "700",
    lineHeight: 19,
  },
  microMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
  },
  microMeta: {
    color: "rgba(255, 255, 255, 0.85)",
    fontSize: 11,
    fontWeight: "600",
  },
  packCard: {
    width: 220,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.2)",
    padding: 16,
    marginRight: 12,
    gap: 8,
    shadowColor: "#000000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
    elevation: 4,
  },
  packCardPressed: {
    opacity: 0.85,
    transform: [{ scale: 0.98 }],
  },
  packIconWell: {
    width: 40,
    height: 40,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255, 255, 255, 0.18)",
  },
  packTitle: {
    color: "#FFFFFF",
    fontSize: 15,
    fontWeight: "700",
  },
  packDescription: {
    color: "rgba(255, 255, 255, 0.85)",
    fontSize: 12,
    lineHeight: 17,
  },
  packBadge: {
    alignSelf: "flex-start",
    borderRadius: 999,
    backgroundColor: "rgba(255, 255, 255, 0.22)",
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginTop: 2,
  },
  packBadgeText: {
    color: "#FFFFFF",
    fontSize: 11,
    fontWeight: "700",
  },
  libraryCard: {
    padding: 14,
  },
  libraryPressable: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  libraryIconWell: {
    width: 44,
    height: 44,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.auroraTealSoft,
  },
  libraryInfo: {
    flex: 1,
    gap: 2,
  },
  libraryTitle: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "600",
  },
  libraryMeta: {
    color: colors.textTertiary,
    fontSize: 12,
    textTransform: "capitalize",
  },
  lockOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    borderRadius: 24,
    backgroundColor: "rgba(15, 23, 42, 0.62)",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  lockWell: {
    width: 36,
    height: 36,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(248, 250, 252, 0.16)",
  },
  lockLabel: {
    color: "#F8FAFC",
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.5,
    textTransform: "uppercase",
  },
  difficultyBadge: {
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  difficultyText: {
    fontSize: 11,
    fontWeight: "700",
  },
});
