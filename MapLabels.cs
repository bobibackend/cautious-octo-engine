using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Oxide.Core;
using Oxide.Core.Plugins;
using UnityEngine;

namespace Oxide.Plugins
{
    [Info("MapLabels", "ServerCheker", "1.5.0")]
    [Description("Exports all monument labels to JSON using MonumentFinder API")]
    public class MapLabels : RustPlugin
    {
        [PluginReference]
        private Plugin MonumentFinder;
        private readonly Dictionary<string, string> MonumentDisplayNames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            // Основные монументы
            ["airfield_1"] = "Airfield",
            ["bandit_town"] = "Bandit Camp",
            ["compound"] = "Outpost",
            ["excavator_1"] = "Giant Excavator",
            ["ferry_terminal_1"] = "Ferry Terminal",
            ["fishing_village_a"] = "Fishing Village",
            ["fishing_village_b"] = "Fishing Village",
            ["fishing_village_c"] = "Fishing Village",
            ["gas_station_1"] = "Gas Station",
            ["harbor_1"] = "Harbor",
            ["harbor_2"] = "Harbor",
            ["junkyard_1"] = "Junkyard",
            ["launch_site_1"] = "Launch Site",
            ["lighthouse"] = "Lighthouse",
            ["military_tunnel_1"] = "Military Tunnel",
            ["oilrig_1"] = "Oil Rig",
            ["oilrig_2"] = "Large Oil Rig",
            ["powerplant_1"] = "Power Plant",
            ["radtown_small_3"] = "Sewer Branch",
            ["satellite_dish"] = "Satellite Dish",
            ["sphere_tank"] = "Dome",
            ["stables_a"] = "Ranch",
            ["stables_b"] = "Ranch",
            ["supermarket_1"] = "Supermarket",
            ["swamp_a"] = "Swamp",
            ["swamp_b"] = "Swamp",
            ["swamp_c"] = "Swamp",
            ["trainyard_1"] = "Train Yard",
            ["warehouse"] = "Warehouse",
            ["water_treatment_plant_1"] = "Water Treatment Plant",
            
            // Пещеры
            ["cave_large_sewers_hard"] = "Sewer Branch",
            ["cave_medium_medium"] = "Cave",
            ["cave_small_easy"] = "Cave",
            ["cave_small_hard"] = "Cave",
            ["cave_small_medium"] = "Cave",
            
            // Бункеры
            ["entrance_bunker_a"] = "Tunnel Entrance",
            ["entrance_bunker_b"] = "Tunnel Entrance",
            ["entrance_bunker_c"] = "Tunnel Entrance",
            ["entrance_bunker_d"] = "Tunnel Entrance",
            
            // Подводные лабы
            ["underwater_lab_a"] = "Underwater Lab",
            ["underwater_lab_b"] = "Underwater Lab",
            ["underwater_lab_c"] = "Underwater Lab",
            ["underwater_lab_d"] = "Underwater Lab",
            
            // Карьеры
            ["mining_quarry_a"] = "Mining Quarry",
            ["mining_quarry_b"] = "Mining Quarry",
            ["mining_quarry_c"] = "Mining Quarry",
            
            // Ледяные озёра
            ["ice_lake_1"] = "Ice Lake",
            ["ice_lake_2"] = "Ice Lake",
            
            // Руины джунглей
            ["jungle_ruins_a"] = "Jungle Ruins",
            ["jungle_ruins_b"] = "Jungle Ruins",
            ["jungle_ruins_c"] = "Jungle Ruins",
            ["jungle_ruins_d"] = "Jungle Ruins",
            ["jungle_ruins_e"] = "Jungle Ruins",
            ["jungle_ziggurat_a"] = "Jungle Temple",
            
            // Военные базы
            ["desert_military_base_a"] = "Desert Military Base",
            ["desert_military_base_b"] = "Desert Military Base",
            ["desert_military_base_c"] = "Desert Military Base",
            
            // Арктические базы
            ["arctic_research_base_a"] = "Arctic Research Base",
            ["arctic_research_base_b"] = "Arctic Research Base",
            
            // Прочее
            ["radtown_1"] = "Abandoned Cabins",
            ["train_tunnel_double_entrance"] = "Train Tunnel Entrance",
            ["train_tunnel_double_entrance_36m"] = "Train Tunnel Entrance"
        };

        private readonly HashSet<string> HiddenMonuments = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            // Электростанции (мусор)
            "power_sub_big_1",
            "power_sub_big_2",
            "power_sub_small_1",
            "power_sub_small_2",
            
            // Колодцы (мусор)
            "water_well_a",
            "water_well_b",
            "water_well_c",
            "water_well_d",
            "water_well_e",
            
            // Входы в тоннели
            "entrance_bunker_a",
            "entrance_bunker_b",
            "entrance_bunker_c",
            "entrance_bunker_d",
            
            // Болота
            "swamp_a",
            "swamp_b",
            "swamp_c"
        };

        private readonly Dictionary<string, Vector3> MonumentOffsets = new Dictionary<string, Vector3>(StringComparer.OrdinalIgnoreCase)
        {
            ["airfield_1"] = new Vector3(0, 0, -25), ["bandit_town"] = new Vector3(0, 0, -5),
            ["gas_station_1"] = new Vector3(0, 0, 15), ["harbor_1"] = new Vector3(-8, 0, 15),
            ["harbor_2"] = new Vector3(6, 0, 18), ["launch_site_1"] = new Vector3(10, 0, -26),
            ["lighthouse"] = new Vector3(10f, 0, 5), ["military_tunnel_1"] = new Vector3(0, 0, -25),
            ["oilrig_1"] = new Vector3(3, 0, 12), ["oilrig_2"] = new Vector3(18, 0, -2),
            ["powerplant_1"] = new Vector3(-15, 0, -11), ["radtown_small_3"] = new Vector3(-10, 0, -18),
            ["satellite_dish"] = new Vector3(0, 0, 3), ["stables_a"] = new Vector3(0, 0, 4),
            ["stables_b"] = new Vector3(2, 0, 6), ["supermarket_1"] = new Vector3(1, 0, 1),
            ["swamp_a"] = new Vector3(-10, 0, 0), ["trainyard_1"] = new Vector3(10, 0, -30),
            ["warehouse"] = new Vector3(0, 0, -8), ["water_treatment_plant_1"] = new Vector3(20, 0, -45)
        };

        private void Init() => PrintWarning("MapLabels: Plugin loaded. Using MonumentFinder API for data.");

        private void OnServerInitialized()
        {
            timer.Once(20f, () => {
                ConsoleSystem.Run(ConsoleSystem.Option.Server, "world.rendermap");
                timer.Once(60f, () => {
                    try { ExportLabels(); }
                    catch (Exception ex) { PrintError($"MapLabels error: {ex.Message}"); }
                    timer.Once(10f, () => ConsoleSystem.Run(ConsoleSystem.Option.Server, "quit"));
                });
            });
        }

        private void ExportLabels()
        {
            if (MonumentFinder == null || !MonumentFinder.IsLoaded)
            {
                PrintError("MapLabels: MonumentFinder plugin not loaded! Cannot export labels.");
                return;
            }

            var labels = new List<MapLabel>();
            
            // Получаем только нормальные монументы (не тоннели, не подводные лабы)
            var monumentsData = MonumentFinder.Call("API_FindMonuments", "") as List<Dictionary<string, object>>;
            
            if (monumentsData == null || monumentsData.Count == 0)
            {
                PrintWarning("MapLabels: No monuments found via MonumentFinder API.");
            }
            else
            {
                PrintWarning($"MapLabels: Found {monumentsData.Count} monuments via MonumentFinder API.");
                
                foreach (var monumentDict in monumentsData)
                {
                    try
                    {
                        string shortName = monumentDict["ShortName"] as string;
                        string alias = monumentDict["Alias"] as string;
                        string prefabName = monumentDict["PrefabName"] as string;
                        Vector3 position = (Vector3)monumentDict["Position"];
                        
                        // Пропускаем модули подводных лаб и другой мусор
                        if (shortName.Contains("module_") || 
                            shortName.Contains("tube_") || 
                            shortName.Contains("moonpool_") ||
                            shortName.StartsWith("train_tunnel_double_entrance"))
                            continue;
                        
                        // Используем красивое название если есть, иначе Alias, иначе ShortName
                        string displayName = shortName;
                        if (MonumentDisplayNames.TryGetValue(shortName, out var prettyName))
                        {
                            displayName = prettyName;
                        }
                        else if (!string.IsNullOrEmpty(alias))
                        {
                            displayName = alias;
                        }
                        
                        // Применяем смещения если есть
                        if (MonumentOffsets.TryGetValue(shortName, out var offset))
                        {
                            var transformPoint = monumentDict["TransformPoint"] as Func<Vector3, Vector3>;
                            if (transformPoint != null)
                            {
                                position = transformPoint(offset);
                            }
                        }
                        
                        // Проверяем нужно ли скрывать на карте
                        bool hideOnMap = HiddenMonuments.Contains(shortName);
                        
                        labels.Add(new MapLabel 
                        { 
                            Name = displayName, 
                            X = position.x, 
                            Z = position.z, 
                            IsCustom = false,
                            ShortName = shortName,
                            PrefabName = prefabName,
                            HideOnMap = hideOnMap
                        });
                    }
                    catch (Exception ex)
                    {
                        PrintError($"MapLabels: Error processing monument: {ex.Message}");
                    }
                }
            }

            // Добавляем кастомные маркеры
            foreach (var entity in BaseNetworkable.serverEntities)
            {
                if (entity is MapMarkerGenericRadius)
                {
                    var m = entity as MapMarkerGenericRadius;
                    string mName = m.gameObject.name;
                    if (string.IsNullOrEmpty(mName) || mName.Contains(".prefab")) mName = m.ShortPrefabName;
                    
                    if (!string.IsNullOrEmpty(mName) && !mName.Contains(".prefab"))
                        labels.Add(new MapLabel { Name = mName, X = entity.transform.position.x, Z = entity.transform.position.z, IsCustom = true, HideOnMap = false });
                }
                else if (entity is VendingMachineMapMarker)
                {
                    var v = entity as VendingMachineMapMarker;
                    if (!string.IsNullOrEmpty(v.markerShopName))
                        labels.Add(new MapLabel { Name = v.markerShopName, X = entity.transform.position.x, Z = entity.transform.position.z, IsCustom = true, HideOnMap = true }); // Скрываем магазины на карте
                }
            }

            var data = new MapData { WorldSize = TerrainMeta.Size.x, Labels = labels };
            Interface.Oxide.DataFileSystem.WriteObject("MapLabels", data);
            PrintWarning($"MapLabels: Exported {labels.Count} labels total.");
        }

        private string GetAnyName(MonumentInfo monument)
        {
            // 1. Пытаемся взять английское название
            if (monument.displayPhrase != null && !string.IsNullOrEmpty(monument.displayPhrase.english))
                return monument.displayPhrase.english;

            // 2. Имя объекта
            if (!string.IsNullOrEmpty(monument.name) && !monument.name.Contains("/") && !monument.name.Contains(".prefab"))
                return monument.name;

            // 3. Имя родителя
            if (monument.transform.parent != null && !monument.transform.parent.name.Contains(".prefab"))
                return monument.transform.parent.name;

            // 4. Имя корня
            if (monument.transform.root != null && !monument.transform.root.name.Contains(".prefab"))
                return monument.transform.root.name;

            // 5. Стандартный маппинг (если ничего другого нет)
            return GetMonumentNameFromPrefab(monument.name);
        }

        private string GetShortName(string prefabPath)
        {
            if (string.IsNullOrEmpty(prefabPath)) return "";
            var parts = prefabPath.Split('/');
            return parts.Last().Replace(".prefab", "");
        }

        private string GetMonumentNameFromPrefab(string prefabPath)
        {
            var nameMap = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "airfield", "Airfield" }, { "bandit_town", "Bandit Camp" }, { "compound", "Outpost" },
                { "excavator", "Giant Excavator" }, { "ferry_terminal", "Ferry Terminal" },
                { "fishing_village", "Fishing Village" }, { "gas_station", "Gas Station" },
                { "harbor", "Harbor" }, { "junkyard", "Junkyard" }, { "large_oil_rig", "Large Oil Rig" },
                { "launch_site", "Launch Site" }, { "lighthouse", "Lighthouse" },
                { "military_tunnel", "Military Tunnel" }, { "oil_rig", "Oil Rig" },
                { "power_plant", "Power Plant" }, { "satellite_dish", "Satellite Dish" },
                { "sewer_branch", "Sewer Branch" }, { "sphere_tank", "Dome" },
                { "stables", "Ranch" }, { "supermarket", "Supermarket" },
                { "trainyard", "Train Yard" }, { "water_treatment_plant", "Water Treatment" }
            };

            string lower = prefabPath.ToLower();
            foreach (var kvp in nameMap)
                if (lower.Contains(kvp.Key)) return kvp.Value;

            return null;
        }

        private class MapData { public float WorldSize; public List<MapLabel> Labels; }
        private class MapLabel 
        { 
            public string Name; 
            public float X; 
            public float Z; 
            public bool IsCustom;
            public string ShortName;
            public string PrefabName;
            public bool HideOnMap;
        }
    }
}
