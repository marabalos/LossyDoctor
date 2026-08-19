import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from reporting.markdown_report import write_md
class Reporting(unittest.TestCase):
 def test_validity_domains_and_seek_interpretation_are_human_visible(self):
  run={'app_version':'0.44.0','run_id':'x','started_at':'x','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[{
   'display_name':'x.mp3','run_status':'SUCCESS_WITH_FINDINGS','detected_container':'MPEG_AUDIO','detected_codec':'mp3','format_confidence':'HIGH','playability':'PLAYABLE','pcm_recovery_class':'NOT_ASSESSED',
   'decode_results':{'STRICT_DECODE':{'passed':True},'PLAYBACK_DECODE':{'passed':True}},'canonical_presentation_window':{'determined':True,'logical_sample_count':1,'logical_duration_seconds':1},
   'format_facts':{'mpeg_version':1,'layer':3,'frame_count':2,'bitrate_mode':'VBR','vbr_header':{'xing':{'kind':'Xing','frames':1,'bytes':100,'encoder':'LAME'}},'ffprobe':{'duration':'1'}},
   'validity_domains':{'DECODE_VALIDITY':'VALID','TIMELINE_VALIDITY':'VALID','SEEKABILITY_VALIDITY':'NONCONFORMANT_METADATA'},
   'pattern_analysis':{'scope':'SINGLE_FILE','issue_count':1,'distinct_issue_code_count':1,'groups':[{'issue_code':'XING_BYTE_COUNT_MISMATCH','occurrence_count':1,'observation':'ISOLATED','known_byte_range_count':0}]},
   'causal_graph':{'conclusion':'NO_CAUSAL_RELATIONSHIP_PROVEN','unresolved_observed_issue_codes':['XING_BYTE_COUNT_MISMATCH']},
   'issues':[{'code':'XING_BYTE_COUNT_MISMATCH','layer':'seek_metadata','description':'x','evidence_nature':'OBJECTIVE','confidence':'HIGH','integrity':'NONCONFORMANT','playability':'UNAFFECTED','repairability':'SAFE_IF_VERIFIED','byte_start':None,'byte_end':None}],
   'final_status':['ANOMALY_UNCHANGED']}], 'toolchain':{}}
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r.md';write_md(p,run);t=p.read_text(encoding="utf-8")
   self.assertIn('**Dominios de validez**',t);self.assertIn('SEEKABILITY_VALIDITY: `NONCONFORMANT_METADATA`',t);self.assertIn('reparabilidad `SAFE_IF_VERIFIED`',t);self.assertIn('Los originales nunca se modifican',t);self.assertIn('**Patrones observados de hallazgos**',t);self.assertIn('NO_CAUSAL_RELATIONSHIP_PROVEN',t)
 def test_recovery_assessment_is_human_visible(self):
  run={'app_version':'0.44.0','run_id':'x','started_at':'x','summary':{'discovered':1,'processed':1,'ok':0,'with_findings':1,'skipped':0,'failed':0,'repaired_outputs_created':0,'lossless_outputs_created':0,'outputs_reused':0,'candidates_rejected':0},'files':[{
   'display_name':'damaged.mp3','run_status':'SUCCESS_WITH_FINDINGS','detected_container':'MPEG_AUDIO','detected_codec':'mp3','format_confidence':'HIGH','playability':'PLAYABLE','pcm_recovery_class':'PARTIAL_CLEAN',
   'decode_results':{'STRICT_DECODE':{'passed':False,'error_output_present':True},'PLAYBACK_DECODE':{'passed':True,'error_output_present':True},'SALVAGE_DECODE':{'passed':True,'error_output_present':True}},'canonical_presentation_window':{'determined':False},
   'format_facts':{'mpeg_version':1,'layer':3,'frame_count':10,'bitrate_mode':'CBR','vbr_header':{},'ffprobe':{'duration':'1'}},
   'validity_domains':{'DECODE_VALIDITY':'DEGRADED','TIMELINE_VALIDITY':'NONCONFORMANT','SEEKABILITY_VALIDITY':'NO_DEDICATED_METADATA'},
   'recovery_assessment':{'required':True,'pcm_class':'PARTIAL_CLEAN','confidence':'HIGH','clean_region_count':1,'header_repair_simulation':{'attempted':False},'recommended_strategies':[{'id':'PARTIAL_LOSSLESS_PCM_WITH_TIMELINE_PROVENANCE','requires_audio_recoding':True,'timeline_gap_policy':'INSERT_ZERO_SILENCE_ONLY_IF_MISSING_DURATION_IS_OBJECTIVELY_KNOWN; OTHERWISE_SPLIT_PARTS'}]},
   'issues':[{'code':'MPEG_SYNC_LOSS','layer':'framing','description':'x','evidence_nature':'OBJECTIVE','confidence':'HIGH','integrity':'DAMAGED','playability':'DEGRADED','repairability':'RECOVERY_ONLY','byte_start':10,'byte_end':20}], 'final_status':['ANOMALY_UNCHANGED']}], 'toolchain':{}}
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'r.md';write_md(p,run);t=p.read_text(encoding="utf-8")
   self.assertIn('STRICT_DECODE: `FAIL`',t);self.assertIn('PLAYBACK_DECODE: `PASS`',t);self.assertIn('SALVAGE_DECODE: `PASS`',t);self.assertIn('PCM: `PARTIAL_CLEAN`',t);self.assertIn('MPEG_SYNC_LOSS',t)
