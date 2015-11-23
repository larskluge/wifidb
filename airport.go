package main

import (
	"log"
	"os"
	"os/exec"
	"regexp"
)

func Airport(data *Info) {
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

	data.Ssid = matches[4]
	data.Bssid = matches[3]
	data.Auth = matches[1]
	data.LinkAuth = matches[2]
}
