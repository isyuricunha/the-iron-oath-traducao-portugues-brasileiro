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
            Console.Error.WriteLine("usage: DllStringPatch list <in.dll> <out.json>");
            Console.Error.WriteLine("       DllStringPatch seq <in.dll> <type> <out.json>");
            Console.Error.WriteLine("       DllStringPatch trace <in.dll> <type> <method> <out.txt>");
            Console.Error.WriteLine("       DllStringPatch patch <in.dll> <map.json> <out.dll>");
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
        // Debug: verify a stub type resolves and exposes value__.
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
                    if (!parameter.HasDefault || parameter.Constant is null)
                    {
                        continue;
                    }
                    if (parameter.ParameterType.Scope is not AssemblyNameReference)
                    {
                        continue;
                    }
                    try
                    {
                        var resolved = parameter.ParameterType.Resolve();
                        Console.Error.WriteLine(
                            $"debug resolve {parameter.ParameterType.FullName} -> {resolved?.FullName} fields={resolved?.Fields.Count}");
                    }
                    catch (Exception ex)
                    {
                        Console.Error.WriteLine($"debug resolve {parameter.ParameterType.FullName} -> EX {ex.Message}");
                        continue;
                    }
                    break;
                }
                break;
            }
            break;
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
            foreach (var method in type.Methods)
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
            CollectStrings(type, found);
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
}
