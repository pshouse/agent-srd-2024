/**
 * AND across keys, OR across values of one key (same rule as srd_list).
 * @param {{ facets?: Record<string, string[]> }} entity
 * @param {Record<string, string[]> | null | undefined} selected
 * @returns {boolean}
 */
export function entityMatchesFacets(entity, selected) {
  if (!selected) return true;
  const facets = entity.facets || {};
  for (const [key, wanted] of Object.entries(selected)) {
    if (!wanted || wanted.length === 0) continue;
    const have = facets[key];
    if (!Array.isArray(have)) return false;
    const present = new Set(have);
    if (!wanted.some((value) => present.has(value))) return false;
  }
  return true;
}

/**
 * @param {string} value
 * @returns {string}
 */
export function facetLabel(value) {
  const text = String(value).replace(/[_-]+/g, " ").trim();
  if (/^-?\d+(\.\d+)?$/.test(text)) return text;
  return text
    .split(/\s+/)
    .map((word) => {
      if (word.length <= 2) return word.toUpperCase();
      return word.charAt(0).toUpperCase() + word.slice(1);
    })
    .join(" ");
}

/**
 * Distinct values for one facet key, with counts. Numeric values sort numerically.
 * @param {Array<{ facets?: Record<string, string[]> }>} entities
 * @param {string} key
 * @returns {{ value: string, count: number }[]}
 */
export function valuesForFacet(entities, key) {
  const counts = new Map();
  for (const entity of entities) {
    const vals = entity.facets && entity.facets[key];
    if (!Array.isArray(vals)) continue;
    for (const value of vals) {
      if (typeof value !== "string" || value === "") continue;
      counts.set(value, (counts.get(value) || 0) + 1);
    }
  }
  const values = [...counts.keys()];
  const numeric = values.length > 0 && values.every((value) => /^-?\d+(\.\d+)?$/.test(value));
  values.sort((a, b) => {
    if (numeric) return Number(a) - Number(b);
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  });
  return values.map((value) => ({ value, count: counts.get(value) }));
}

/**
 * @param {{ resource_types?: Array<{ type: string, count?: number, facet_keys?: string[] }> }} manifest
 * @returns {{ type: string, count: number, facetKeys: string[] }[]}
 */
/** Five families. Shape and colour travel together; the label is always text. */
export const FAMILIES = [
  {
    id: "rules",
    label: "Core rules",
    types: [
      "ability-scores",
      "alignments",
      "conditions",
      "damage-types",
      "languages",
      "magic-schools",
      "proficiencies",
      "skills",
      "weapon-mastery-properties",
      "weapon-properties",
    ],
  },
  {
    id: "chars",
    label: "Character options",
    types: [
      "backgrounds",
      "classes",
      "feats",
      "features",
      "levels",
      "species",
      "subclasses",
      "subspecies",
      "traits",
    ],
  },
  {
    id: "gear",
    label: "Gear & items",
    types: ["equipment", "equipment-categories", "magic-items", "poisons"],
  },
  { id: "spell", label: "Spells", types: ["spells"] },
  { id: "mon", label: "Monsters", types: ["monsters"] },
];

const FAMILY_BY_TYPE = new Map(FAMILIES.flatMap((family) => family.types.map((type) => [type, family])));

const TYPE_LABELS = {
  "ability-scores": "Ability score",
  alignments: "Alignment",
  backgrounds: "Background",
  classes: "Class",
  conditions: "Condition",
  "damage-types": "Damage type",
  equipment: "Equipment",
  "equipment-categories": "Equipment category",
  feats: "Feat",
  features: "Feature",
  languages: "Language",
  levels: "Level",
  "magic-items": "Magic item",
  "magic-schools": "Magic school",
  monsters: "Monster",
  poisons: "Poison",
  proficiencies: "Proficiency",
  skills: "Skill",
  species: "Species",
  spells: "Spell",
  subclasses: "Subclass",
  subspecies: "Subspecies",
  traits: "Trait",
  "weapon-mastery-properties": "Weapon mastery",
  "weapon-properties": "Weapon property",
};

const FACET_LABELS = {
  attack_type: "Attack type",
  cost_unit: "Cost unit",
  creature_type: "Creature type",
  damage_type: "Damage type",
  feat_type: "Feat type",
  has_prerequisite: "Has prerequisite",
  has_subspecies: "Has subspecies",
  hit_die: "Hit die",
  is_rare: "Rare",
  limited_to: "Limited to",
  poison_type: "Poison type",
  proficiency_type: "Proficiency type",
  saving_throw: "Saving throw",
  cr: "Challenge rating",
};

/** Facet keys shown before the rest. The first three become the open groups. */
export const FACET_PRIORITY = ["level", "class", "school", "cr", "rarity", "creature_type", "category", "size"];

const CR_FRACTIONS = { 0.125: "1/8", 0.25: "1/4", 0.5: "1/2" };

/**
 * @param {string} type
 */
export function familyOf(type) {
  return FAMILY_BY_TYPE.get(type) || { id: "rules", label: "Core rules", types: [] };
}

/**
 * @param {string} type
 */
export function typeLabel(type) {
  return TYPE_LABELS[type] || facetLabel(type);
}

/**
 * @param {string} type
 */
export function typePlural(type) {
  return facetLabel(type);
}

/**
 * @param {string} key
 */
export function facetKeyLabel(key) {
  return FACET_LABELS[key] || facetLabel(key);
}

/**
 * Friendly facet value. Level 0 is Cantrip, booleans are Yes/No, common CRs are fractions.
 * @param {string} key
 * @param {string} value
 */
export function facetValueLabel(key, value) {
  if (key === "level" && value === "0") return "Cantrip";
  if (value === "true") return "Yes";
  if (value === "false") return "No";
  if (key === "cr" && CR_FRACTIONS[value]) return CR_FRACTIONS[value];
  if (/^-?\d+(\.\d+)?$/.test(value)) return value;
  return facetLabel(value);
}

/**
 * @param {string[]} keys
 */
export function orderFacetKeys(keys) {
  const present = new Set(keys);
  const first = FACET_PRIORITY.filter((key) => present.has(key));
  const rest = keys.filter((key) => !FACET_PRIORITY.includes(key)).sort();
  return [...first, ...rest];
}

export function resourceTypesFromManifest(manifest) {
  const rows = manifest && Array.isArray(manifest.resource_types) ? manifest.resource_types : [];
  return rows
    .filter((row) => row && typeof row.type === "string")
    .map((row) => ({
      type: row.type,
      count: typeof row.count === "number" ? row.count : 0,
      facetKeys: Array.isArray(row.facet_keys) ? row.facet_keys.filter((key) => typeof key === "string") : [],
    }));
}
