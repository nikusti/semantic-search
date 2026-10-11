// SPDX-License-Identifier: MIT
// Native JSON-lines bridge for text, image, video and audio inference.
@preconcurrency import CoreML
import Foundation
import Darwin

func monotonic() -> Double { Double(DispatchTime.now().uptimeNanoseconds) / 1e9 }
func memory() -> [String:Double] {
 var info=task_vm_info_data_t();var count=mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size/4)
 withUnsafeMutablePointer(to:&info){p in p.withMemoryRebound(to:integer_t.self,capacity:Int(count)){_=task_info(mach_task_self_,task_flavor_t(TASK_VM_INFO),$0,&count)}}
 var usage=rusage();getrusage(RUSAGE_SELF,&usage)
 return ["rss_mib":Double(info.resident_size)/1048576,"footprint_mib":Double(info.phys_footprint)/1048576,"lifetime_peak_rss_mib":Double(usage.ru_maxrss)/1048576]
}
func reply(_ values:[String:Any]) {let data=try! JSONSerialization.data(withJSONObject:values);print(String(decoding:data,as:UTF8.self));fflush(stdout)}
func normalized(_ v:[Float])->[Float] {let a=Array(v);let n=sqrt(a.reduce(Float(0)){$0+$1*$1});return a.map{$0/n}}
func floats(_ path:URL)throws->[Float]{try Data(contentsOf:path).withUnsafeBytes{Array($0.bindMemory(to:Float.self))}}
func ints(_ path:URL)throws->[Int32]{try Data(contentsOf:path).withUnsafeBytes{Array($0.bindMemory(to:Int32.self))}}
func array(_ values:[Float],_ shape:[Int])throws->MLMultiArray{
 let a=try MLMultiArray(shape:shape.map(NSNumber.init(value:)),dataType:.float16)
 values.withUnsafeBufferPointer{a.dataPointer.assumingMemoryBound(to:Float16.self).initialize(from:$0.map(Float16.init),count:values.count)}
 return a
}
struct Desc:Decodable{let inputs:[String:Item];struct Item:Decodable{let shape:[Int];let dtype:String}}
@main struct Worker {
 static func main() async {
  do {try await run()}catch{reply(["error":String(describing:error),"memory":memory()]);exit(1)}
 }
 static func run() async throws {
  let args=CommandLine.arguments
  if args.count >= 4 && args[1] == "--compile" {
   let root=URL(fileURLWithPath:args[2])
   for name in args.dropFirst(3) {
    let target=root.appendingPathComponent(name+".mlmodelc")
    if !FileManager.default.fileExists(atPath:target.path) {
     let compiled=try await MLModel.compileModel(at:root.appendingPathComponent(name+".mlpackage"))
     try FileManager.default.copyItem(at:compiled,to:target)
    }
   }
   reply(["compiled":true]);return
  }
  guard args.count == 4 else { throw EmbeddingGemma2Error.invalidAsset("Expected model, media and worker mode") }
  let preparing=args[3].hasPrefix("prepare-")
  let mode=preparing ? String(args[3].dropFirst("prepare-".count)):args[3]
  guard ["search","index","audio"].contains(mode) else { throw EmbeddingGemma2Error.invalidAsset("Unknown worker mode") }
  let assets=URL(fileURLWithPath:args[1]);let media=URL(fileURLWithPath:args[2]);let start=monotonic()
  let text=try await EmbeddingGemma2Manager.load(from:assets)
  var vision:[Int:MLModel]=[:];var positions:[Float]=[]
  if mode=="index" {
   positions=try Data(contentsOf:media.appendingPathComponent("position_embeddings.f16")).withUnsafeBytes{$0.bindMemory(to:Float16.self).map(Float.init)}
   for budget in [70,140,280]{let cfg=MLModelConfiguration();cfg.computeUnits = .cpuAndGPU;cfg.functionName="vision_\(budget)";vision[budget]=try await MLModel.load(contentsOf:media.appendingPathComponent("EmbeddingGemma2Vision.mlmodelc"),configuration:cfg)}
  }
  var audio:MLModel?=nil
  if mode=="audio" {
   let cfg=MLModelConfiguration();cfg.computeUnits = .cpuAndGPU
   audio=try await MLModel.load(contentsOf:media.appendingPathComponent("EmbeddingGemma2Audio.mlmodelc"),configuration:cfg)
  }
  if preparing {
   try await text.prepare()
   reply(["event":"prepared","seconds":monotonic()-start,"text_functions":await text.loadedFunctionNames()]);return
  }
  reply(["event":"ready","load_seconds":monotonic()-start,"memory":memory(),"vision_loaded":!vision.isEmpty,"text_functions":await text.loadedFunctionNames()])
  while let line=readLine(){
   do {
    let req=try JSONSerialization.jsonObject(with:Data(line.utf8)) as! [String:Any];let op=req["op"] as! String;let t=monotonic()
    if op=="close"{reply(["event":"closing","memory":memory()]);return}
    var vectors:[[Float]]=[]
    if op=="query" {vectors=[normalized(try await text.embed(req["text"] as! String,prompt:.searchQuery))]}
    else if op=="documents" {vectors=try await text.embed(req["texts"] as! [String],prompt:.document(title:nil)).map(normalized)}
    else if op=="audio" {
     guard let audio else { throw EmbeddingGemma2Error.predictionFailed("Audio encoder is not loaded") }
     let folder=URL(fileURLWithPath:req["folder"] as! String)
     let ids=try ints(folder.appendingPathComponent("input_ids.bin"))
     let samples=try floats(folder.appendingPathComponent("waveform.bin"))
     guard !samples.isEmpty && samples.count<=160000 else { throw EmbeddingGemma2Error.predictionFailed("Audio windows must contain at most 10 seconds at 16 kHz") }
     var wave=[Float](repeating:0,count:160160);wave.replaceSubrange(160..<(160+samples.count),with:samples)
     let waveform=try MLMultiArray(shape:[1,160160],dataType:.float32)
     wave.withUnsafeBufferPointer{waveform.dataPointer.assumingMemoryBound(to:Float.self).update(from:$0.baseAddress!,count:wave.count)}
     let frames=max(0,min(1000,(160+samples.count-321)/160+1))
     guard frames>0 else {throw EmbeddingGemma2Error.predictionFailed("Audio window is too short")}
     let mask=try array((0..<1000).map{$0<frames ? 1:0},[1,1000])
     let input=try MLDictionaryFeatureProvider(dictionary:["waveform":waveform,"frame_mask":mask])
     let output=try await audio.prediction(from:input)
     let tokens=output.featureValue(for:"audio_tokens")!.multiArrayValue!
     let count=(frames+3)/4;let stride=tokens.strides[tokens.strides.count-2].intValue
     let features=(0..<count).flatMap{r in (0..<512).map{c in let i=r*stride+c;return tokens.dataType == .float16 ? Float(tokens.dataPointer.assumingMemoryBound(to:Float16.self)[i]):tokens.dataPointer.assumingMemoryBound(to:Float.self)[i]}}
     vectors=[normalized(try await text.embedMultimodal(ids:ids,features:features,placeholder:258881))]
    }
    else if op=="image" || op=="video" {
     let folder=URL(fileURLWithPath:req["folder"] as! String)
     let desc=try JSONDecoder().decode(Desc.self,from:Data(contentsOf:folder.appendingPathComponent("inputs.json")))
     let ids=try ints(folder.appendingPathComponent("input_ids.bin"))
     let video=op=="video";let pixelKey=video ? "pixel_values_videos":"pixel_values";let positionKey=video ? "video_position_ids":"image_position_ids"
     let patches=try floats(folder.appendingPathComponent(pixelKey+".bin"));let allPositions=try ints(folder.appendingPathComponent(positionKey+".bin"))
     let d=desc.inputs[pixelKey]!;let n=d.shape[d.shape.count-2];let budget=n/9
     let frameCount=patches.count/(n*768);var allFeatures:[Float]=[]
     for frame in 0..<frameCount {
     let pos=Array(allPositions[frame*n*2..<(frame+1)*n*2])
     guard let model=vision[budget] else{throw EmbeddingGemma2Error.predictionFailed("unsupported vision budget \(budget)")}
     var valid=[Float](repeating:0,count:n);var pe=[Float](repeating:0,count:n*768);var pool=[Float](repeating:0,count:budget*n)
     let width=Int((stride(from:0,to:pos.count,by:2).map{pos[$0]}.max() ?? 0)+1);var actual=0
     for j in 0..<n where pos[j*2]>=0{
      let x=Int(pos[j*2]),y=Int(pos[j*2+1]);valid[j]=1
      for c in 0..<768{pe[j*768+c]=positions[x*768+c]+positions[(1024+y)*768+c]}
      let group=x/3+(width/3)*(y/3);pool[group*n+j]=1/9;actual=max(actual,group+1)
     }
     let input=try MLDictionaryFeatureProvider(dictionary:["patches":try array(Array(patches[frame*n*768..<(frame+1)*n*768]),[1,n,768]),"positions":try array(pos.map(Float.init),[1,n,2]),"position_embeddings":try array(pe,[1,n,768]),"valid":try array(valid,[1,n]),"pool":try array(pool,[budget,n])])
     let out=try await model.prediction(from:input);let a=out.featureValue(for:"image_tokens")!.multiArrayValue!;let stride=a.strides[a.strides.count-2].intValue
     let features=(0..<actual).flatMap{r in (0..<512).map{c in let i=r*stride+c;return a.dataType == .float16 ? Float(a.dataPointer.assumingMemoryBound(to:Float16.self)[i]):a.dataPointer.assumingMemoryBound(to:Float.self)[i]}}
     allFeatures += features
     }
     let placeholder:Int32=video ? 258884:258880
     guard ids.filter({$0==placeholder}).count*512==allFeatures.count else{throw EmbeddingGemma2Error.predictionFailed("Visual feature count mismatch")}
     vectors=[normalized(try await text.embedMultimodal(ids:ids,features:allFeatures,placeholder:placeholder))]
    }else{throw EmbeddingGemma2Error.predictionFailed("unknown request")}
    reply(["vectors":vectors,"seconds":monotonic()-t,"memory":memory(),"text_functions":await text.loadedFunctionNames()])
   }catch{reply(["error":String(describing:error),"memory":memory()])}
  }
 }
}
