package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"regexp"

	"gopkg.in/yaml.v2"
)

type Info struct {
	Ssid     string
	Bssid    string
	Auth     string
	LinkAuth string
	Password string
}

func main() {
	cmd := "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/A/Resources/airport"
	out, err := exec.Command(cmd, "--getinfo").Output()
	if err != nil {
		log.Printf("Error occured while getting airport network information: %v", err)
	}
	info := string(out)
	re := regexp.MustCompile(`(?m:802.11 auth: (.*)$\s*link auth: (.*)$\s*BSSID: (.*)$\s*SSID: (.*)$)`)
	matches := re.FindStringSubmatch(info)
	if len(matches) == 0 {
		log.Fatal("Wifi info not in expected format--please raise an issue with the following data:")
		log.Fatal(info)
		os.Exit(1)
	}

	thing := Info{
		Ssid:     matches[4],
		Bssid:    matches[3],
		Auth:     matches[1],
		LinkAuth: matches[2],
	}

	str, err := yaml.Marshal(&thing)
	if err != nil {
		log.Fatal("Error while marshalling: %v", err)
		os.Exit(2)
	} else {
		fmt.Println(string(str))
	}
}
