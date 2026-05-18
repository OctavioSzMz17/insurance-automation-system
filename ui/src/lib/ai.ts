/**
 * The agent's brand identity.
 *
 * Centralised so we can rename the assistant or skin the avatar in one
 * place. The name itself is intentionally NOT translated — "Lía" is a
 * proper noun. Decision labels and taglines live in messages/*.json so
 * they follow the active locale.
 */

export const AI_BRAND = {
  name: 'Lía',
  shortName: 'Lía',
  // Single-character avatar fallback when no image is available.
  avatarInitial: 'L',
} as const;
