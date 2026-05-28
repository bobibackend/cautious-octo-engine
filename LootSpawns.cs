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

            // Найти все LootContainer объекты (реальные ящики, которые ты поставил в RustEdit)
            var allContainers = UnityEngine.Object.FindObjectsOfType<LootContainer>();
            Puts($"Found {allContainers.Length} loot containers");

            foreach (var container in allContainers)
            {
                if (container == null) continue;

                var prefabName = container.ShortPrefabName;
                var position = container.transform.position;
                
                string spawnType = GetSpawnType(prefabName);
                
                if (!string.IsNullOrEmpty(spawnType))
                {
                    spawns.Add(new LootSpawn
                    {
                        Type = spawnType,
                        X = position.x,
                        Z = position.z,
                        PrefabName = prefabName
                    });
                }
            }

            // Найти все OreResourceEntity (камни руды, которые ты поставил в RustEdit)
            var allOres = UnityEngine.Object.FindObjectsOfType<OreResourceEntity>();
            Puts($"Found {allOres.Length} ore nodes");

            foreach (var ore in allOres)
            {
                if (ore == null) continue;

                var prefabName = ore.ShortPrefabName;
                var position = ore.transform.position;
                
                string spawnType = GetOreType(prefabName);
                
                if (!string.IsNullOrEmpty(spawnType))
                {
                    spawns.Add(new LootSpawn
                    {
                        Type = spawnType,
                        X = position.x,
                        Z = position.z,
                        PrefabName = prefabName
                    });
                }
            }

            // Найти все спавны игроков через тег "spawnpoint"
            var spawnPointObjects = GameObject.FindGameObjectsWithTag("spawnpoint");
            Puts($"Found {spawnPointObjects.Length} player spawn points");

            foreach (var spawnObj in spawnPointObjects)
            {
                if (spawnObj == null) continue;

                var position = spawnObj.transform.position;
                
                spawns.Add(new LootSpawn
                {
                    Type = "Player Spawn",
                    X = position.x,
                    Z = position.z,
                    PrefabName = "player_spawn"
                });
            }

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

        private string GetSpawnType(string prefabName)
        {
            // Elite Crates
            if (prefabName.Contains("crate_elite")) return "Elite Crate";
            
            // Military Crates
            if (prefabName.Contains("crate_normal_2") || 
                prefabName.Contains("crate_normal_2_military")) return "Military Crate";
            
            // Normal Crates
            if (prefabName.Contains("crate_normal") && !prefabName.Contains("crate_normal_2")) return "Normal Crate";
            
            // Food Crates
            if (prefabName.Contains("crate_food_")) return "Food Crate";
            
            // Medical Crates
            if (prefabName.Contains("crate_medical")) return "Medical Crate";
            
            // Tool Crates
            if (prefabName.Contains("crate_tools")) return "Tool Crate";
            
            // Oil Barrels (красные с топливом)
            if (prefabName.Contains("oil_barrel")) return "Oil Barrel";
            
            // Regular Barrels (обычные)
            if (prefabName.Contains("barrel")) return "Barrel";
            
            // Minecart (в туннелях)
            if (prefabName.Contains("minecart")) return "Minecart";
            
            // Underwater crates
            if (prefabName.Contains("crate_underwater")) return "Underwater Crate";
            
            return null;
        }

        private string GetOreType(string prefabName)
        {
            // Большие камни руды (которые ты ставишь в RustEdit)
            if (prefabName.Contains("stone-ore")) return "Stone Node";
            if (prefabName.Contains("metal-ore")) return "Metal Node";
            if (prefabName.Contains("sulfur-ore")) return "Sulfur Node";
            
            return null;
        }
    }
}
