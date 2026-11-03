"""Exercise searchable region/feature catalogs before any Android mod loads."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
ECJ = Path('/workspace/.chits-tools/ecj.jar')


class RegionCatalogTests(unittest.TestCase):
    def test_extracted_table_metadata_and_stable_country_ids(self):
        data = json.loads((PROJECT / 'integration/regions.json').read_text())
        self.assertEqual(data['sourceSha256'], '9f34642dd5d62f96cc84bdf2a1e44f198f78fb5e813944ac1058714abef551bc')
        self.assertEqual(data['count'], 191)
        countries = data['regions']
        self.assertEqual([row['index'] for row in countries], list(range(191)))
        self.assertEqual(len({row['iso'] for row in countries}), 191)
        by = next(row for row in countries if row['iso'] == 'BY')
        self.assertEqual((by['index'], by['id'], by['mccMnc'], by['operator']), (13, 14, '25701', 'A1'))
        self.assertNotIn('RU', [row['iso'] for row in countries])
        self.assertNotIn('KG', [row['iso'] for row in countries])

    @unittest.skipUnless(shutil.which('java') and (shutil.which('javac') or ECJ.is_file()), 'Java compiler/runtime required')
    def test_search_labels_immutability_and_feature_scope_without_mod(self):
        java = '''package app.weqrom;
import java.util.*;
public class CatalogHarness {
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 public static void main(String[] args){
  check(RegionCatalog.all().size()==191,"Incomplete country table");
  Set<String> codes=new HashSet<String>();
  for(RegionCatalog.Entry e:RegionCatalog.all()){
   check(codes.add(e.iso),"Duplicate ISO");
   check(RegionCatalog.all().get(e.index)==e,"Index mismatch");
   check(!e.label("ru").isEmpty()&&!e.label("en").isEmpty(),"Missing label");
  }
  RegionCatalog.Entry by=RegionCatalog.find("by");
  check(by.index==13&&by.id==14&&"25701".equals(by.mccMnc),"Belarus operator mismatch");
  check("Беларусь".equals(by.label("ru"))&&"Belarus".equals(by.label("en")),"Belarus labels mismatch");
  check(RegionCatalog.find("RU")==null&&RegionCatalog.find("KG")==null,"Invented unsupported source country");
  check(RegionCatalog.search("БЕЛАРУСЬ","en").get(0)==by,"Russian search failed");
  check(RegionCatalog.search("belarus","ru").get(0)==by,"English search failed");
  check(RegionCatalog.search(" BY ","ru").contains(by),"ISO search failed");
  check(RegionCatalog.search("austral","en").size()==1,"Prefix search failed");
  check(RegionCatalog.search("no-such-country-123","ru").isEmpty(),"No-result search failed");
  check(RegionCatalog.search("", "en").size()==191,"Empty search lost countries");
  try{RegionCatalog.all().clear();throw new AssertionError("Mutable static table");}catch(UnsupportedOperationException expected){}
  check(FeatureCatalog.all().size()==10,"Incomplete feature list");
  Set<String> features=new HashSet<String>();
  for(FeatureCatalog.Entry entry:FeatureCatalog.all())check(features.add(entry.key),"Duplicate feature key");
  check(FeatureCatalog.find("hide_ads").defaultEnabled,"Source ad setting default differs");
  check(FeatureCatalog.find("auto_updates_enable")==null&&FeatureCatalog.find("statistics_enable")==null,"Unrequested updater/telemetry controls added");
  System.out.println("191 searchable countries and checked optional feature catalog passed");
 }
}'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / 'CatalogHarness.java'
            harness.write_text(java)
            compiled = root / 'classes'
            if shutil.which('javac'):
                compiler = ['javac', '-source', '8', '-target', '8', '-Xlint:-options']
            else:
                compiler = ['java', '-jar', str(ECJ), '-8', '-proc:none', '-warn:none']
            source = PROJECT / 'app/src/main/java/app/weqrom'
            subprocess.run([*compiler, '-d', str(compiled), str(source/'RegionCatalog.java'), str(source/'FeatureCatalog.java'), str(harness)], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', str(compiled), 'app.weqrom.CatalogHarness'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
