import React, { useEffect, useRef, useState } from 'react';
import {
  StyleSheet,
  Text,
  View,
  TouchableOpacity,
  SafeAreaView,
  AppState,
  AppStateStatus,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { useGameStore } from './stores/gameStore';
import { GAME } from './constants/game';
import GameScreen from './screens/GameScreen';
import HeroesScreen from './screens/HeroesScreen';
import ShopScreen from './screens/ShopScreen';

type Tab = 'game' | 'heroes' | 'shop';

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('game');
  const init = useGameStore((s) => s.init);
  const save = useGameStore((s) => s.save);
  const applyDps = useGameStore((s) => s.applyDps);
  const [ready, setReady] = useState(false);

  // 초기화
  useEffect(() => {
    init().then(() => setReady(true));
  }, []);

  // DPS 틱 (100ms)
  useEffect(() => {
    if (!ready) return;
    const interval = setInterval(() => {
      applyDps();
    }, 100);
    return () => clearInterval(interval);
  }, [ready]);

  // 자동 저장 (30초)
  useEffect(() => {
    if (!ready) return;
    const interval = setInterval(() => {
      save();
    }, GAME.SAVE_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [ready]);

  // 앱 백그라운드 시 저장
  useEffect(() => {
    const sub = AppState.addEventListener('change', (state: AppStateStatus) => {
      if (state === 'background' || state === 'inactive') {
        save();
      }
    });
    return () => sub.remove();
  }, []);

  if (!ready) {
    return (
      <View style={styles.loading}>
        <Text style={styles.loadingText}>⚔️ 던전 클리커 ⚔️</Text>
        <Text style={styles.loadingSubtext}>로딩 중...</Text>
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />

      {/* 메인 콘텐츠 */}
      <View style={styles.content}>
        {activeTab === 'game' && <GameScreen />}
        {activeTab === 'heroes' && <HeroesScreen />}
        {activeTab === 'shop' && <ShopScreen />}
      </View>

      {/* 하단 탭 바 */}
      <View style={styles.tabBar}>
        <TouchableOpacity
          style={[styles.tab, activeTab === 'game' && styles.tabActive]}
          onPress={() => setActiveTab('game')}
        >
          <Text style={styles.tabEmoji}>⚔️</Text>
          <Text style={[styles.tabText, activeTab === 'game' && styles.tabTextActive]}>던전</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tab, activeTab === 'heroes' && styles.tabActive]}
          onPress={() => setActiveTab('heroes')}
        >
          <Text style={styles.tabEmoji}>🦸</Text>
          <Text style={[styles.tabText, activeTab === 'heroes' && styles.tabTextActive]}>영웅</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.tab, activeTab === 'shop' && styles.tabActive]}
          onPress={() => setActiveTab('shop')}
        >
          <Text style={styles.tabEmoji}>🏪</Text>
          <Text style={[styles.tabText, activeTab === 'shop' && styles.tabTextActive]}>상점</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  loading: {
    flex: 1,
    backgroundColor: '#1a1a2e',
    alignItems: 'center',
    justifyContent: 'center',
  },
  loadingText: {
    fontSize: 28,
    color: '#fff',
    fontWeight: 'bold',
  },
  loadingSubtext: {
    fontSize: 16,
    color: '#888',
    marginTop: 10,
  },
  content: {
    flex: 1,
  },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: '#16213e',
    borderTopWidth: 1,
    borderTopColor: '#2a2a4e',
    paddingBottom: 4,
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 8,
  },
  tabActive: {
    borderTopWidth: 2,
    borderTopColor: '#50fa7b',
  },
  tabEmoji: {
    fontSize: 22,
  },
  tabText: {
    color: '#666',
    fontSize: 11,
    marginTop: 2,
  },
  tabTextActive: {
    color: '#50fa7b',
    fontWeight: 'bold',
  },
});
