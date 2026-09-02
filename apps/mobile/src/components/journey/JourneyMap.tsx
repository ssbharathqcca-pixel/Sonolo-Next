/**
 * C8 Journey Map: three bands, six units each, driven by GET /learn/journey.
 */
import { useEffect, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import type { JourneyBand, JourneyMapData, JourneyUnit } from "../../api/client";
import { colors } from "../../theme/colors";
import { BandCard } from "./BandCard";

export function JourneyMap({
  journey,
  onUnitPress,
}: {
  journey: JourneyMapData;
  onUnitPress: (unit: JourneyUnit) => void;
}): JSX.Element {
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>(() =>
    defaultExpanded(journey),
  );

  useEffect(() => {
    setExpandedIds(defaultExpanded(journey));
  }, [journey]);

  const handleToggle = (band: JourneyBand): void => {
    if (band.status === "locked") {
      return;
    }
    if (band.status === "active") {
      return;
    }
    setExpandedIds((current) => ({
      ...current,
      [band.id]: !current[band.id],
    }));
  };

  return (
    <View style={styles.section} testID="journey-map">
      <Text style={styles.heading}>Your Journey</Text>
      <Text style={styles.subheading}>
        Foundation, Middle, and Advanced — six units in each band.
      </Text>
      {journey.bands.map((band) => (
        <BandCard
          key={band.id}
          band={band}
          expanded={expandedIds[band.id] === true}
          onToggle={handleToggle}
          onUnitPress={onUnitPress}
        />
      ))}
    </View>
  );
}

function defaultExpanded(journey: JourneyMapData): Record<string, boolean> {
  const next: Record<string, boolean> = {};
  for (const band of journey.bands) {
    next[band.id] = band.status === "active";
  }
  return next;
}

const styles = StyleSheet.create({
  section: {
    gap: 10,
  },
  heading: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
    marginTop: 8,
  },
  subheading: {
    color: colors.textTertiary,
    fontSize: 13,
    lineHeight: 18,
  },
});
