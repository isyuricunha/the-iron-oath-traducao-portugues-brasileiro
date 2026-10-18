// DllStringPatch: scoped ldstr string replacement for Assembly-CSharp.dll.
//
// Modes:
//   list  <in.dll> <out.json>          dump every ldstr operand grouped by
//                                      declaring type full name
//   seq   <in.dll> <type> <out.json>   per-method ldstr sequences for one
//                                      type (composition context)
//   patch <in.dll> <map.json> <out.dll>
//                                      replace ldstr operands inside the
//                                      scoped types; unmatched keys fail
//
// The map JSON shape for patch:
//   { "types": { "DifficultyOptionsScreen": { "Starting Point": "Ponto de Partida" } } }
//
// Only Mono.Cecil metadata rewriting is used; the game runtime (Mono)
// loads the result. Strings outside the listed types are never touched.

using Mono.Cecil;
using Mono.Cecil.Cil;
using System.Text.Json;

internal static class Program
{
    // The game assemblies reference Unity libraries that are not present
    // next to Assembly-CSharp.dll in this repository. Resolution failures
    // are irrelevant for ldstr patching, so they are swallowed.
    private sealed class TolerantResolver : BaseAssemblyResolver
    {
        // In-memory enum stubs for scopes that are not on disk (Unity
        // libraries); the writer only needs the constant's type code.
        public readonly Dictionary<string, AssemblyDefinition> Stubs = new(StringComparer.Ordinal);

        private AssemblyDefinition? StubFor(AssemblyNameReference name) =>
            Stubs.TryGetValue(name.Name, out var stub) ? stub : null;

        public override AssemblyDefinition Resolve(AssemblyNameReference name)
        {
            try
            {
                return base.Resolve(name);
            }
            catch (AssemblyResolutionException)
            {
                return StubFor(name);
            }
        }

        public override AssemblyDefinition Resolve(AssemblyNameReference name, ReaderParameters parameters)
        {
            try
            {
                return base.Resolve(name, parameters);
            }
            catch (AssemblyResolutionException)
            {
                return StubFor(name);
            }
        }
    }

    private static ReaderParameters ReadParams(TolerantResolver? resolver = null) =>
        new() { AssemblyResolver = resolver ?? new TolerantResolver() };

    private static int Main(string[] args)
    {
        try
        {
            if (args.Length == 3 && args[0] == "list")
            {
                return RunList(args[1], args[2]);
            }
            if (args.Length == 4 && args[0] == "seq")
            {
                return RunSeq(args[1], args[2], args[3]);
            }
            if (args.Length == 5 && args[0] == "trace")
            {
                return RunTrace(args[1], args[2], args[3], args[4]);
            }
            if (args.Length == 4 && args[0] == "patch")
            {
                return RunPatch(args[1], args[2], args[3]);
            }
            if (args.Length == 4 && args[0] == "usages")
            {
                return RunUsages(args[1], args[2], args[3]);
            }
            Console.Error.WriteLine("usage: DllStringPatch list <in.dll> <out.json>");
            Console.Error.WriteLine("       DllStringPatch seq <in.dll> <type> <out.json>");
            Console.Error.WriteLine("       DllStringPatch trace <in.dll> <type> <method> <out.txt>");
            Console.Error.WriteLine("       DllStringPatch patch <in.dll> <map.json> <out.dll>");
            Console.Error.WriteLine("       DllStringPatch usages <in.dll> <map.json> <out.json>");
            return 64;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"error: {ex}");
            return 1;
        }
    }

    private static int RunSeq(string inputPath, string typeName, string outputPath)
    {
        using var assembly = AssemblyDefinition.ReadAssembly(inputPath, ReadParams());
        var type = assembly.MainModule.GetTypes().Single(t => t.FullName == typeName || t.Name == typeName);
        var result = new SortedDictionary<string, List<string>>(StringComparer.Ordinal);
        foreach (var method in type.Methods)
        {
            if (!method.HasBody)
            {
                continue;
            }
            var sequence = new List<string>();
            foreach (var instruction in method.Body.Instructions)
            {
                if (instruction.OpCode == OpCodes.Ldstr && instruction.Operand is string value)
                {
                    sequence.Add(value);
                }
            }
            if (sequence.Count > 0)
            {
                result[$"{method.Name}"] = sequence;
            }
        }
        var options = new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
        File.WriteAllText(outputPath, JsonSerializer.Serialize(result, options));
        return 0;
    }

    private static int RunTrace(string inputPath, string typeName, string methodName, string outputPath)
    {
        using var assembly = AssemblyDefinition.ReadAssembly(inputPath, ReadParams());
        var type = assembly.MainModule.GetTypes().Single(t => t.FullName == typeName || t.Name == typeName);
        var method = type.Methods.Single(m => m.Name == methodName);
        using var writer = new StreamWriter(outputPath);
        if (!method.HasBody)
        {
            writer.WriteLine("(no body)");
            return 0;
        }
        foreach (var instruction in method.Body.Instructions)
        {
            var operand = instruction.Operand switch
            {
                string s => $"\"{s}\"",
                MethodReference mr => mr.Name,
                FieldReference fr => fr.Name,
                Instruction target => $"IL_{target.Offset:X4}",
                null => "",
                var op => op.ToString(),
            };
            writer.WriteLine($"IL_{instruction.Offset:X4}: {instruction.OpCode.Name} {operand}");
        }
        return 0;
    }

    private static void CollectConstantStub(
        TypeReference constantType, bool hasDefault, object? constant,
        SortedDictionary<string, List<(string Ns, string Name)>> stubTypes)
    {
        if (!hasDefault || constant is null)
        {
            return;
        }
        if (constantType.Scope is not AssemblyNameReference scope || scope.Name == "Assembly-CSharp")
        {
            return;
        }
        try
        {
            var resolved = constantType.Resolve();
            if (resolved is not null)
            {
                return;
            }
            throw new InvalidOperationException("unresolved");
        }
        catch
        {
            if (!stubTypes.TryGetValue(scope.Name, out var list))
            {
                stubTypes[scope.Name] = list = new List<(string, string)>();
            }
            var full = constantType.FullName;
            var cut = full.LastIndexOf('.');
            var entry = cut < 0 ? ("", full) : (full[..cut], full[(cut + 1)..]);
            if (!list.Contains(entry))
            {
                list.Add(entry);
            }
        }
    }

    private static void BuildConstantStubs(AssemblyDefinition assembly, TolerantResolver resolver)
    {
        // Find parameter default constants whose types live in scopes we
        // cannot resolve; each needs the writer to learn only its
        // underlying type code, so a minimal enum stub is enough.
        var stubTypes = new SortedDictionary<string, List<(string Ns, string Name)>>(StringComparer.Ordinal);
        foreach (var type in assembly.MainModule.GetTypes())
        {
            foreach (var method in type.Methods)
            {
                if (!method.HasParameters)
                {
                    continue;
                }
                foreach (var parameter in method.Parameters)
                {
                    CollectConstantStub(parameter.ParameterType, parameter.HasDefault, parameter.Constant, stubTypes);
                }
            }
            foreach (var field in type.Fields)
            {
                CollectConstantStub(field.FieldType, field.HasDefault, field.Constant, stubTypes);
            }
        }
        foreach (var (scopeName, types) in stubTypes)
        {
            var stub = AssemblyDefinition.CreateAssembly(
                new AssemblyNameDefinition(scopeName, new Version(0, 0, 0, 0)), scopeName, ModuleKind.Dll);
            var module = stub.MainModule;
            foreach (var (ns, name) in types)
            {
                var enumRef = new TypeReference("System", "Enum", module, null);
                // TypeSystem.Int32 carries the element type the writer's
                // constant switch needs, so it resolves without I/O.
                var intRef = module.TypeSystem.Int32;
                var definition = new TypeDefinition(ns, name, TypeAttributes.Public | TypeAttributes.Sealed, enumRef);
                // Cecil's GetEnumUnderlyingType returns the first NON-static
                // field, matching how real enums declare value__.
                definition.Fields.Add(new FieldDefinition(
                    "value__",
                    FieldAttributes.Public | FieldAttributes.RTSpecialName | FieldAttributes.SpecialName,
                    intRef));
                module.Types.Add(definition);
            }
            resolver.Stubs[scopeName] = stub;
        }
    }

    private static IEnumerable<TypeDefinition> WithNested(TypeDefinition type)
    {
        // Compiler-generated closure classes (<c__DisplayClass*, <Async*>)
        // are nested inside their logical scope type and carry ldstr
        // sites of their own, so scope replacement must descend.
        yield return type;
        foreach (var nested in type.NestedTypes)
        {
            foreach (var inner in WithNested(nested))
            {
                yield return inner;
            }
        }
    }

    private static int RunList(string inputPath, string outputPath)
    {
        using var assembly = AssemblyDefinition.ReadAssembly(inputPath, ReadParams());
        var byType = new SortedDictionary<string, Dictionary<string, int>>(StringComparer.Ordinal);
        foreach (var type in assembly.MainModule.GetTypes())
        {
            var strings = new Dictionary<string, int>(StringComparer.Ordinal);
            CollectStrings(type, strings);
            if (strings.Count > 0)
            {
                byType[type.FullName] = strings;
            }
        }
        var options = new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
        File.WriteAllText(outputPath, JsonSerializer.Serialize(byType, options));
        return 0;
    }

    private static void CollectStrings(TypeDefinition type, Dictionary<string, int> strings)
    {
        foreach (var method in type.Methods)
        {
            if (!method.HasBody)
            {
                continue;
            }
            foreach (var instruction in method.Body.Instructions)
            {
                if (instruction.OpCode == OpCodes.Ldstr && instruction.Operand is string value)
                {
                    strings[value] = strings.TryGetValue(value, out var count) ? count + 1 : 1;
                }
            }
        }
    }

    private static int RunPatch(string inputPath, string mapPath, string outputPath)
    {
        var json = JsonDocument.Parse(File.ReadAllText(mapPath));
        var scopes = new Dictionary<string, Dictionary<string, string>>(StringComparer.Ordinal);
        foreach (var typeProp in json.RootElement.GetProperty("types").EnumerateObject())
        {
            var map = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var pair in typeProp.Value.EnumerateObject())
            {
                map[pair.Name] = pair.Value.GetString() ?? throw new InvalidOperationException($"null target for {pair.Name}");
            }
            scopes[typeProp.Name] = map;
        }

        var resolver = new TolerantResolver();
        using var assembly = AssemblyDefinition.ReadAssembly(inputPath, ReadParams(resolver));
        var typesByName = new Dictionary<string, TypeDefinition>(StringComparer.Ordinal);
        foreach (var type in assembly.MainModule.GetTypes())
        {
            typesByName[type.FullName] = type;
            typesByName.TryAdd(type.Name, type);
        }

        var applied = new Dictionary<string, int>(StringComparer.Ordinal);
        foreach (var (typeName, map) in scopes)
        {
            if (!typesByName.TryGetValue(typeName, out var type))
            {
                Console.Error.WriteLine($"error: type not found: {typeName}");
                return 2;
            }
            foreach (var method in WithNested(type).SelectMany(t => t.Methods))
            {
                if (!method.HasBody)
                {
                    continue;
                }
                foreach (var instruction in method.Body.Instructions)
                {
                    if (instruction.OpCode != OpCodes.Ldstr || instruction.Operand is not string value)
                    {
                        continue;
                    }
                    if (map.TryGetValue(value, out var target))
                    {
                        instruction.Operand = target;
                        applied[$"{typeName}|{value}"] = applied.TryGetValue($"{typeName}|{value}", out var count) ? count + 1 : 1;
                    }
                }
            }
        }

        // Every requested key must have replaced at least one instruction;
        // a stale mapping must fail loudly instead of shipping silently.
        var missing = new List<string>();
        foreach (var (typeName, map) in scopes)
        {
            foreach (var source in map.Keys)
            {
                if (!applied.ContainsKey($"{typeName}|{source}"))
                {
                    missing.Add($"{typeName}: {source}");
                }
            }
        }
        if (missing.Count > 0)
        {
            Console.Error.WriteLine("error: keys with no ldstr site:");
            foreach (var key in missing)
            {
                Console.Error.WriteLine($"  {key}");
            }
            return 3;
        }

        // Parameters with enum default values from missing scopes make
        // the writer resolve their types; register enum stubs for those
        // scopes first so the write succeeds without the real libraries.
        BuildConstantStubs(assembly, resolver);
        assembly.Write(outputPath);

        // Verify by re-reading: every scoped EN string is gone and every
        // PT string is present inside its scope.
        using var verify = AssemblyDefinition.ReadAssembly(outputPath, ReadParams());
        foreach (var (typeName, map) in scopes)
        {
            var type = verify.MainModule.GetTypes().Single(t => t.FullName == typeName || t.Name == typeName);
            var found = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (var scopeType in WithNested(type))
            {
                CollectStrings(scopeType, found);
            }
            foreach (var (source, target) in map)
            {
                if (found.ContainsKey(source))
                {
                    Console.Error.WriteLine($"error: {typeName} still contains {source}");
                    return 4;
                }
                if (!found.ContainsKey(target))
                {
                    Console.Error.WriteLine($"error: {typeName} lacks patched value {target}");
                    return 4;
                }
            }
        }

        Console.WriteLine(JsonSerializer.Serialize(new { applied, total = applied.Values.Sum() }));
        return 0;
    }

    // Scan every mapped (scope, en) string and report the sites where the
    // game COMPARES it: input-action checks, pronoun/save-value switches,
    // enum parse and string-keyed collection lookups. Translating any of
    // those ldstr operands breaks the comparison at runtime, so the map
    // must not touch them. Display-only sites (stfld/call as a value) are
    // ignored. List<int> get_Item is array access, not a string lookup.
    private static int RunUsages(string inputPath, string mapPath, string outputPath)
    {
        using var assembly = AssemblyDefinition.ReadAssembly(inputPath, ReadParams());
        var mapJson = JsonDocument.Parse(File.ReadAllText(mapPath));
        var map = mapJson.RootElement.GetProperty("dll");

        var findings = new SortedSet<string>(StringComparer.Ordinal);
        int checkedEntries = 0;
        foreach (var cls in map.EnumerateObject())
        {
            var scope = cls.Name;
            var type = assembly.MainModule.GetTypes()
                .SingleOrDefault(t => t.FullName == scope || t.Name == scope);
            if (type == null)
            {
                continue;
            }
            foreach (var entry in cls.Value.EnumerateObject())
            {
                var en = entry.Name;
                checkedEntries++;
                foreach (var method in type.Methods)
                {
                    if (!method.HasBody)
                    {
                        continue;
                    }
                    var instructions = method.Body.Instructions;
                    for (var i = 0; i < instructions.Count; i++)
                    {
                        if (instructions[i].OpCode != OpCodes.Ldstr || (string)instructions[i].Operand != en)
                        {
                            continue;
                        }
                        if (IsLogicUse(method, instructions, i))
                        {
                            findings.Add(scope + "\u0001" + en + "\u0001" + method.Name);
                        }
                    }
                }
            }
        }

        var pairs = findings
            .Select(f => f.Split('\u0001'))
            .Select(p => new[] { p[0], p[1], p[2] });
        var options = new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        };
        File.WriteAllText(outputPath, JsonSerializer.Serialize(pairs, options));
        Console.WriteLine($"checked={checkedEntries} logic_usages={findings.Count}");
        return 0;
    }

    private static bool IsLogicUse(MethodDefinition method, IList<Instruction> instructions, int siteIndex)
    {
        // Direct window: the ldstr feeds a comparison within 15 instructions
        // before being consumed as a value (stfld/stsfld/call ends the chain).
        for (var j = siteIndex + 1; j < Math.Min(siteIndex + 15, instructions.Count); j++)
        {
            var instruction = instructions[j];
            if (instruction.OpCode.Code == Code.Switch || IsLogicCall(instruction.Operand as MethodReference))
            {
                return true;
            }
            if (instruction.OpCode == OpCodes.Bne_Un || instruction.OpCode == OpCodes.Bne_Un_S
                || instruction.OpCode == OpCodes.Beq || instruction.OpCode == OpCodes.Beq_S)
            {
                return true;
            }
            var code = instruction.OpCode.Code;
            if (code == Code.Stfld || code == Code.Stsfld || code == Code.Call || code == Code.Callvirt)
            {
                break;
            }
        }

        // Local dataflow: ldstr -> stloc, later read into a comparison.
        VariableDefinition? stored = null;
        var storeIndex = -1;
        for (var j = siteIndex + 1; j < Math.Min(siteIndex + 6, instructions.Count); j++)
        {
            var opcode = instructions[j].OpCode;
            if (opcode == OpCodes.Stloc || opcode == OpCodes.Stloc_S
                || (instructions[j].OpCode.Code >= Code.Stloc_0 && instructions[j].OpCode.Code <= Code.Stloc_3))
            {
                storeIndex = j;
                stored = ResolveVariable(method, instructions[j]);
                break;
            }
            var chainCode = instructions[j].OpCode.Code;
            if (chainCode == Code.Stfld || chainCode == Code.Stsfld || chainCode == Code.Call || chainCode == Code.Callvirt)
            {
                break;
            }
        }
        if (storeIndex < 0 || stored == null)
        {
            return false;
        }
        for (var j = storeIndex + 1; j < instructions.Count; j++)
        {
            var instruction = instructions[j];
            var loads = instruction.OpCode == OpCodes.Ldloc || instruction.OpCode == OpCodes.Ldloc_S
                || (instruction.OpCode.Code >= Code.Ldloc_0 && instruction.OpCode.Code <= Code.Ldloc_3);
            if (!loads || ResolveVariable(method, instruction) != stored)
            {
                continue;
            }
            for (var k = j + 1; k < Math.Min(j + 6, instructions.Count); k++)
            {
                var consumer = instructions[k];
                if (consumer.OpCode.Code == Code.Switch || IsLogicCall(consumer.Operand as MethodReference))
                {
                    return true;
                }
                var code = consumer.OpCode.Code;
                if (code == Code.Stfld || code == Code.Stsfld || code == Code.Call || code == Code.Callvirt)
                {
                    break;
                }
            }
        }
        return false;
    }

    private static bool IsLogicCall(MethodReference? callee)
    {
        if (callee == null)
        {
            return false;
        }
        var full = callee.FullName ?? string.Empty;
        if (full.Contains("System.String::Equals") || full.Contains("String::Equals")
            || full.Contains("op_Equality") || full.Contains("op_Inequality")
            || full.Contains("::Contains(") || full.Contains("::StartsWith(") || full.Contains("::EndsWith(")
            || full.Contains("String::Compare") || full.Contains("System.Enum::Parse"))
        {
            return true;
        }
        if (full.Contains("get_Item") || full.Contains("TryGetValue") || full.Contains("ContainsKey")
            || full.Contains("Dictionary`2::Add") || full.Contains("::Remove("))
        {
            return callee.Parameters.Any(p => p.ParameterType.Name == "String");
        }
        // Object lookup by name: translating the operand breaks the lookup
        // (Find returns null, then the first use of it throws NRE).
        if (full.Contains("::Find(") || full.Contains("::FindChild(")
            || (full.Contains("::GetComponent") && callee.HasParameters)
            || full.Contains("::FindFirstAncestor")
            || full.Contains("::FindObjectsOfType(") && callee.HasParameters)
        {
            return callee.Parameters.Any(p => p.ParameterType.Name == "String");
        }
        return false;
    }

    private static VariableDefinition? ResolveVariable(MethodDefinition method, Instruction instruction)
    {
        if (instruction.Operand is VariableDefinition definition)
        {
            return definition;
        }
        var code = instruction.OpCode.Code;
        if (code is Code.Stloc_0 or Code.Ldloc_0)
        {
            return method.Body.Variables[0];
        }
        if (code is Code.Stloc_1 or Code.Ldloc_1)
        {
            return method.Body.Variables[1];
        }
        if (code is Code.Stloc_2 or Code.Ldloc_2)
        {
            return method.Body.Variables[2];
        }
        if (code is Code.Stloc_3 or Code.Ldloc_3)
        {
            return method.Body.Variables[3];
        }
        if (instruction.Operand is int index)
        {
            return method.Body.Variables[index];
        }
        return null;
    }
}
