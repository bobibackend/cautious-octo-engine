using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Oxide.Core;
using UnityEngine;

namespace Oxide.Plugins
{
    [Info("LootSpawns", "Bobik", "1.0.0")]
    [Description("Exports loot spawn locations to JSON")]
    class LootSpawns : RustPlugin
    {
        private class LootSpawn
        {
            public string Type { get; set; }
            public float X { get; set; }
            public float Z { get; set; }
            public string PrefabName { get; set; }
        }

        private class LootSpawnsData
        {
            public int WorldSize { get; set; }
            public List<LootSpawn> Spawns { get; set; }
        }

        void OnServerInitialized()
        {
            timer.Once(5f, () => ExportLootSpawns());
        }

        [ConsoleCommand("lootspawns.export")]
        void ExportLootSpawns()
        {
            var spawns = new List<LootSpawn>();
            var worldSize = ConVar.Server.worldsize;

            Puts("Starting loot spawns export...");

            // Все сущности сервера: ящики (LootContainer, в т.ч. HackableLockedCrate), руда (OreResourceEntity),
            // дизель и карты-пикапы (CollectibleEntity). Спавнеры из RustEdit к этому моменту уже поставили свои объекты.
            int containers = 0, ores = 0, collectibles = 0;
            foreach (var networkable in BaseNetworkable.serverEntities)
            {
                var entity = networkable as BaseEntity;
                if (entity == null || entity.IsDestroyed) continue;

                if (entity is LootContainer) containers++;
                else if (entity is OreResourceEntity) ores++;
                else if (entity is CollectibleEntity) collectibles++;
                else continue;

                var prefabName = entity.ShortPrefabName;
                string spawnType = GetLootType(prefabName);
                if (string.IsNullOrEmpty(spawnType)) continue;

                var position = entity.transform.position;
                spawns.Add(new LootSpawn
                {
                    Type = spawnType,
                    X = position.x,
                    Z = position.z,
                    PrefabName = prefabName
                });
            }
            Puts($"Found {containers} loot containers, {ores} ore nodes, {collectibles} collectibles; exported {spawns.Count}");

            // Точки появления игроков — та же область, из которой берёт точки сам сервер (SpawnHandler)
            AddPlayerSpawns(spawns);

            var data = new LootSpawnsData
            {
                WorldSize = worldSize,
                Spawns = spawns
            };

            var json = JsonConvert.SerializeObject(data, Formatting.Indented);
            var filePath = $"{Interface.Oxide.DataDirectory}/LootSpawns.json";
            System.IO.File.WriteAllText(filePath, json);

            Puts($"Exported {spawns.Count} loot spawns to {filePath}");
            Puts($"Elite Crates: {spawns.Count(s => s.Type == "Elite Crate")}");
            Puts($"Military Crates: {spawns.Count(s => s.Type == "Military Crate")}");
            Puts($"Normal Crates: {spawns.Count(s => s.Type == "Normal Crate")}");
            Puts($"Food Crates: {spawns.Count(s => s.Type == "Food Crate")}");
            Puts($"Medical Crates: {spawns.Count(s => s.Type == "Medical Crate")}");
            Puts($"Tool Crates: {spawns.Count(s => s.Type == "Tool Crate")}");
            Puts($"Barrels: {spawns.Count(s => s.Type == "Barrel")}");
            Puts($"Oil Barrels: {spawns.Count(s => s.Type == "Oil Barrel")}");
            Puts($"Stone Nodes: {spawns.Count(s => s.Type == "Stone Node")}");
            Puts($"Metal Nodes: {spawns.Count(s => s.Type == "Metal Node")}");
            Puts($"Sulfur Nodes: {spawns.Count(s => s.Type == "Sulfur Node")}");
            Puts($"Player Spawns: {spawns.Count(s => s.Type == "Player Spawn")}");
        }

        // ---------------------------------------------------------------- Player Spawns
        // Сервер (ServerMgr.FindSpawnPoint → SpawnHandler.GetSpawnPoint) выбирает случайную точку из CharDistribution:
        // сетка NextPowerOfTwo(World.Size * 0.5), значение > 0 там, где CharacterSpawn.GetFactor > CharacterSpawnCutoff
        // (на стандартном сервере: пляж + Mainland + Oceanside, Tier0/Tier1, биом Temperate/Tundra, без скал/дорог/рек/
        // монументов), и отбрасывает точки ближе 50 м к любому монументу. Если такой области нет —
        // берутся объекты с тегом "spawnpoint" / "SpawnPointFallback".
        // Область выводим равномерной выборкой: одна точка на клетку PlayerSpawnStep x PlayerSpawnStep.
        private const float PlayerSpawnStep = 60f;
        private const float PlayerSpawnMonumentDistance = 50f;

        private void AddPlayerSpawns(List<LootSpawn> spawns)
        {
            int before = spawns.Count;
            try
            {
                AddProceduralPlayerSpawns(spawns);
            }
            catch (Exception ex)
            {
                PrintWarning($"Player spawns: procedural export failed: {ex.Message}");
            }

            if (spawns.Count == before)
            {
                foreach (var tag in new[] { "spawnpoint", "SpawnPointFallback" })
                {
                    GameObject[] objects;
                    try { objects = GameObject.FindGameObjectsWithTag(tag); }
                    catch { continue; }
                    if (objects == null || objects.Length == 0) continue;
                    foreach (var go in objects)
                    {
                        if (go == null) continue;
                        var p = go.transform.position;
                        spawns.Add(new LootSpawn { Type = "Player Spawn", X = p.x, Z = p.z, PrefabName = tag });
                    }
                    break;
                }
            }
            Puts($"Player spawns: {spawns.Count - before}");
        }

        private void AddProceduralPlayerSpawns(List<LootSpawn> spawns)
        {
            var handler = SingletonComponent<SpawnHandler>.Instance;
            if (handler == null || handler.CharacterSpawn == null || TerrainMeta.TopologyMap == null || World.Size == 0)
            {
                PrintWarning("Player spawns: SpawnHandler is not ready");
                return;
            }

            var filter = handler.CharacterSpawn;
            float cutoff = handler.CharacterSpawnCutoff;
            int res = Mathf.NextPowerOfTwo((int)(World.Size * 0.5f));
            Vector3 origin = TerrainMeta.Position;
            Vector3 size = TerrainMeta.Size;
            var monuments = TerrainMeta.Path != null ? TerrainMeta.Path.Monuments : new List<MonumentInfo>();

            int n = Mathf.CeilToInt(size.x / PlayerSpawnStep);
            var cellCount = new int[n * n];
            var sumX = new double[n * n];
            var sumZ = new double[n * n];
            var px = new List<float>();
            var pz = new List<float>();
            var pc = new List<int>();

            for (int z = 0; z < res; z++)
            {
                float normZ = (z + 0.5f) / res;
                for (int x = 0; x < res; x++)
                {
                    float normX = (x + 0.5f) / res;
                    if (filter.GetFactor(normX, normZ) <= cutoff) continue;

                    var pos = new Vector3(origin.x + normX * size.x, 0f, origin.z + normZ * size.z);
                    pos.y = TerrainMeta.HeightMap != null ? TerrainMeta.HeightMap.GetHeight(pos) : 0f;
                    bool nearMonument = false;
                    foreach (var monument in monuments)
                    {
                        if (monument != null && monument.Distance(pos) < PlayerSpawnMonumentDistance) { nearMonument = true; break; }
                    }
                    if (nearMonument) continue;

                    int cx = Mathf.Clamp((int)((pos.x - origin.x) / PlayerSpawnStep), 0, n - 1);
                    int cz = Mathf.Clamp((int)((pos.z - origin.z) / PlayerSpawnStep), 0, n - 1);
                    int c = cz * n + cx;
                    cellCount[c]++;
                    sumX[c] += pos.x;
                    sumZ[c] += pos.z;
                    px.Add(pos.x); pz.Add(pos.z); pc.Add(c);
                }
            }

            // в каждой клетке — подходящая точка, ближайшая к центру масс подходящей области клетки
            var best = new Dictionary<int, int>();
            var bestDist = new Dictionary<int, double>();
            for (int i = 0; i < pc.Count; i++)
            {
                int c = pc[i];
                if (cellCount[c] < 4) continue;
                double dx = px[i] - sumX[c] / cellCount[c];
                double dz = pz[i] - sumZ[c] / cellCount[c];
                double d = dx * dx + dz * dz;
                double prev;
                if (!bestDist.TryGetValue(c, out prev) || d < prev)
                {
                    bestDist[c] = d;
                    best[c] = i;
                }
            }

            // прореживание: точки соседних клеток не ближе PlayerSpawnStep / 2
            float minDist2 = PlayerSpawnStep * PlayerSpawnStep / 4f;
            var kept = new List<Vector2>();
            foreach (var cell in best.Keys.OrderByDescending(c => cellCount[c]).ThenBy(c => c))
            {
                var p = new Vector2(px[best[cell]], pz[best[cell]]);
                if (kept.Any(k => (k - p).sqrMagnitude < minDist2)) continue;
                kept.Add(p);
            }

            foreach (var p in kept.OrderBy(k => k.x).ThenBy(k => k.y))
            {
                spawns.Add(new LootSpawn { Type = "Player Spawn", X = p.x, Z = p.y, PrefabName = "procedural" });
            }
            Puts($"Player spawns: {pc.Count} spawnable cells of {res}x{res}, {kept.Count} points");
        }

        // Тип по ShortPrefabName. Порядок проверок важен; копия loot_type() в fastmap.py.
        // Имена сверены с GameManifest игры (обновление от 02.10.2026).
        private static string GetLootType(string prefabName)
        {
            if (string.IsNullOrEmpty(prefabName)) return null;
            var s = prefabName.ToLowerInvariant();

            // Ключ-карты (на столах, пикапы, спавнеры)
            foreach (var c in new[] { "green", "blue", "red" })
            {
                if (s.Contains(c + "_card") || s.Contains("card_" + c) || s.Contains(c + "card"))
                    return char.ToUpperInvariant(c[0]) + c.Substring(1) + " Card";
            }

            if (s.Contains("hackablecrate")) return "Locked Crate";
            if (s.Contains("crate_elite") || s.Contains("elite_crate")) return "Elite Crate";
            // crate_normal_2_food / crate_normal_2_medical — это еда и медицина, не военный ящик
            if (s.Contains("food") && (s.Contains("crate") || s.Contains("box"))) return "Food Crate";
            if (s.Contains("medical") || s.Contains("med_crate")) return "Medical Crate";
            if (s.Contains("crate_normal_2")) return "Military Crate";
            if (s.Contains("crate_normal") || s.Contains("normal_crates")) return "Normal Crate";
            if (s.Contains("tool") && s.Contains("crate")) return "Tool Crate";
            if (s.Contains("underwater") && s.Contains("crate")) return "Underwater Crate";
            if (s.Contains("vehicle_parts")) return "Vehicle Parts";
            if (s.Contains("tech_parts")) return "Tech Crate";
            if (s.Contains("crate_ammunition") || s.Contains("ammo_crate") || s.Contains("crate_cannons")) return "Ammo Crate";
            if (s.Contains("crate_fuel") || s == "spawner_fuel") return "Fuel Crate";
            if ((s.Contains("basic") && s.Contains("crate")) || s.Contains("crate_mine") || s.Contains("mine_crate") || s.Contains("crate_shore"))
                return "Basic Crate";

            if (s.Contains("diesel")) return "Diesel Barrel";
            if (s.Contains("oil_barrel")) return "Oil Barrel";
            if (s.Contains("barrel")) return "Barrel";
            if (s.Contains("minecart") || s.Contains("mine_cart")) return "Minecart";
            if (s.Contains("trash")) return "Trash Pile";

            // Руда: stone-ore / metal-ore / sulfur-ore / hqm-ore и radtown/ore_*
            if (s.Contains("-ore") || s.StartsWith("ore_"))
            {
                if (s.Contains("hqm")) return "HQM Node";
                if (s.Contains("stone")) return "Stone Node";
                if (s.Contains("metal")) return "Metal Node";
                if (s.Contains("sulfur")) return "Sulfur Node";
                if (s.Contains("random")) return "Random Node";
            }
            return null;
        }
    }
}
