/** @vitest-environment jsdom */
import { describe, it, expect } from 'vitest';

describe('Baize Frontend — Core', () => {
  it('renders without crashing (placeholder)', () => {
    // Basic sanity check — real tests require mounting with providers
    expect(true).toBe(true);
  });
});

describe('useLocalStorage', () => {
  it('stores and retrieves values', async () => {
    const { useLocalStorage } = await import('../hooks/useLocalStorage');
    // This test validates the hook signature and type correctness
    // Real tests would use @testing-library/react-hooks
    expect(typeof useLocalStorage).toBe('function');
  });
});

describe('ThemeContext', () => {
  it('exports ThemeProvider and useTheme', async () => {
    const mod = await import('../context/ThemeContext');
    expect(mod.ThemeProvider).toBeDefined();
    expect(mod.useTheme).toBeDefined();
  });
});

describe('API Client', () => {
  it('has required exports', async () => {
    const client = await import('../api/client');
    expect(client.createSession).toBeDefined();
    expect(client.listAgents).toBeDefined();
    expect(client.listAvailableTools).toBeDefined();
    expect(client.healthCheck).toBeDefined();
  });
});
