/**
 * Sonolo root layout: theme providers plus auth and onboarding gating.
 *
 * The auth store hydrates from the device keychain once on mount; a
 * splash screen holds the fort until it finishes (no flicker). Once
 * hydrated: unauthenticated users see only the (auth) stack, users who
 * have not finished onboarding see only the (onboarding) stack, and
 * everyone else sees the app — session and feedback screens included,
 * so deep links can't bypass the gate.
 *
 * The whole tree sits inside a global error boundary (SN-017), and the
 * Axios client's connectivity handlers drive the offline banner store.
 */

import { useEffect } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { PostHogProvider } from 'posthog-react-native';
import '../lib/analytics'; // Initializes Sentry & exports posthog
import { posthog } from '../lib/analytics';

import {
  resetConnectivityState,
  setConnectivityHandlers,
} from "../src/api/client";
import { AppErrorBoundary } from "../src/components/ErrorBoundary";
import { OfflineBanner } from "../src/components/OfflineBanner";
import { useAuthStore } from "../src/stores/authStore";
import { useNetworkStore } from "../src/stores/networkStore";
import { colors } from "../src/theme/colors";
import { ThemeProvider } from "../src/theme/ThemeProvider";

function SplashScreen(): JSX.Element {
  return (
    <View style={styles.splash}>
      <Text style={styles.splashWordmark}>Sonolo</Text>
      <ActivityIndicator color={colors.auroraTeal} size="large" />
    </View>
  );
}

const stackScreenOptions = {
  headerShown: false,
  contentStyle: { backgroundColor: colors.nightSky },
} as const;

export default function RootLayout(): JSX.Element {
  const hydrate = useAuthStore((state) => state.hydrate);
  const isHydrated = useAuthStore((state) => state.isHydrated);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const onboardingCompleted = useAuthStore(
    (state) => state.onboardingCompleted,
  );

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  // Bridge Axios network events into the offline-banner store.
  useEffect(() => {
    setConnectivityHandlers({
      onOffline: () => useNetworkStore.getState().markOffline(),
      onOnline: () => useNetworkStore.getState().markOnline(),
    });
    return () => {
      setConnectivityHandlers({});
      resetConnectivityState();
    };
  }, []);

  return (
    <AppErrorBoundary>
      <PostHogProvider client={posthog}>
        {!isHydrated ? (
          <SafeAreaProvider>
            <StatusBar style="light" />
            <SplashScreen />
          </SafeAreaProvider>
        ) : (
          <ThemeProvider>
            <SafeAreaProvider>
              <StatusBar style="light" />
              {!isAuthenticated ? (
                <Stack screenOptions={stackScreenOptions}>
                  <Stack.Screen name="(auth)" />
                </Stack>
              ) : !onboardingCompleted ? (
                <Stack screenOptions={stackScreenOptions}>
                  <Stack.Screen name="(onboarding)" />
                </Stack>
              ) : (
                <Stack screenOptions={stackScreenOptions}>
                  <Stack.Screen name="(tabs)" />
                  <Stack.Screen
                    name="pack/[id]"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="session/[id]"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="feedback/[id]"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="microlesson/[id]"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="pronunciation/[id]"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="listening/[id]"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="scorecard"
                    options={{ animation: "slide_from_bottom" }}
                  />
                  <Stack.Screen
                    name="unit/[id]"
                    options={{ animation: "slide_from_right" }}
                  />
                </Stack>
              )}
              <OfflineBanner />
            </SafeAreaProvider>
          </ThemeProvider>
        )}
      </PostHogProvider>
    </AppErrorBoundary>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    backgroundColor: colors.nightSky,
    alignItems: "center",
    justifyContent: "center",
    gap: 24,
  },
  splashWordmark: {
    color: colors.textPrimary,
    fontSize: 40,
    fontWeight: "800",
    letterSpacing: 1,
  },
});